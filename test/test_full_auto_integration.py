"""Full end-to-end integration test: auto calibrate, apply all, verify metadata."""
import os
import shutil
import subprocess
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


class TestFullAutoIntegration(unittest.TestCase):
    """Copy sdcard image, mount, auto calibrate, apply all, verify every metadata field."""

    @classmethod
    def setUpClass(cls):
        cls._work_dir = None
        cls._temp_dir = None
        cls.mount_point = None
        cls.loop_dev = None
        cls.target = None
        cls.median = None
        cls.files_before = {}  # {path: {exif, mtime, btime}}

        gz_path = Path(__file__).parent / 'sdcard.img.gz'
        if not gz_path.exists():
            raise unittest.SkipTest(f'Compressed image not found at {gz_path}')

        from shared import decompress_sparse_image

        # Decompress to the test directory (cached — skip if already present)
        cached = Path(__file__).parent / 'sdcard.img'
        decompress_sparse_image(gz_path, cached)

        # Copy to a temp directory so we never modify the cached image
        cls._work_dir = Path(tempfile.mkdtemp(prefix='gopro_full_test_'))
        cls.img_path = cls._work_dir / 'sdcard.img'
        subprocess.run(['cp', '--sparse=always', str(cached), str(cls.img_path)],
                       check=True, capture_output=True)

        try:
            r = subprocess.run(
                ['udisksctl', 'loop-setup', '-f', str(cls.img_path),
                 '--no-user-interaction'],
                capture_output=True, text=True)
            if r.returncode != 0:
                raise unittest.SkipTest('udisksctl loop-setup failed')
            m = re.search(r'as (/dev/loop\d+)', r.stdout)
            cls.loop_dev = m.group(1) if m else None
            if not cls.loop_dev:
                raise unittest.SkipTest('Could not parse loop device')

            r = subprocess.run(
                ['udisksctl', 'mount', '-b', cls.loop_dev,
                 '--no-user-interaction'],
                capture_output=True, text=True)
            if r.returncode != 0:
                if 'AlreadyMounted' in r.stderr:
                    m = re.search(r"at `([^`]+)'", r.stderr)
                    if m:
                        cls.mount_point = m.group(1)
                if not cls.mount_point:
                    raise unittest.SkipTest('udisksctl mount failed')
            else:
                m = re.search(r'at ([^ \n]+)', r.stdout)
                if m:
                    cls.mount_point = m.group(1).rstrip('.')
                else:
                    raise unittest.SkipTest('Could not parse mount point')
        except FileNotFoundError:
            raise unittest.SkipTest('udisksctl not found')

        cls.target = Path(cls.mount_point) / 'DCIM' / '100GOPRO'
        if not cls.target.exists():
            raise unittest.SkipTest(f'{cls.target} not found')

        # Register cleanup early so tearDown always runs
        cls._temp_dir = cls._work_dir

    @classmethod
    def tearDownClass(cls):
        if cls.loop_dev:
            r = subprocess.run(
                ['udisksctl', 'unmount', '-b', cls.loop_dev,
                 '--no-user-interaction'],
                capture_output=True, text=True)
            if r.returncode != 0:
                subprocess.run(['sudo', 'umount', cls.loop_dev],
                               capture_output=True)
            subprocess.run(['sudo', 'losetup', '-d', cls.loop_dev],
                           capture_output=True)
        if cls._work_dir:
            shutil.rmtree(cls._work_dir, ignore_errors=True)

    # ── Helpers ───────────────────────────────────────────────

    def _read_exif(self, path):
        """Return QuickTime:CreateDate as naive UTC datetime, or None."""
        r = subprocess.run(
            ['exiftool', '-b', '-QuickTime:CreateDate', str(path)],
            capture_output=True, text=True)
        if r.returncode != 0 or not r.stdout.strip():
            return None
        val = r.stdout.strip().splitlines()[0].strip()
        from media import _strip_tz
        val = _strip_tz(val)
        try:
            return datetime.strptime(val, '%Y:%m:%d %H:%M:%S').replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    def _read_mtime(self, path):
        ts = os.path.getmtime(path)
        return datetime.fromtimestamp(ts, tz=timezone.utc)

    def _read_btime(self, path):
        st = os.stat(path)
        bt = getattr(st, 'st_birthtime', None)
        if bt is not None:
            return datetime.fromtimestamp(bt, tz=timezone.utc)
        return None

    def _record_metadata(self, files):
        md = {}
        for f in files:
            md[f] = {
                'exif': self._read_exif(f),
                'mtime': self._read_mtime(f),
                'btime': self._read_btime(f),
            }
        return md

    # ── Tests ─────────────────────────────────────────────────

    def test_full_pipeline(self):
        """Auto calibrate, apply all, verify every metadata field changed by the median delta."""
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

        import media
        import analysis as an_mod
        import preview
        from preview import STRATEGY_MANUAL, SetDecision
        from resolve import weighted_median_delta
        from writer import Writer, WriteJob

        all_files = media.collect(self.target)

        # ── 1. Record original metadata ────────────────────────
        orig = self._record_metadata(all_files)

        # ── 2. Compute weighted median delta (auto calibration) ─
        batch = media.read_tags_batch(all_files)
        accuracy = media.read_gps_accuracy_batch(all_files)

        pairs = []
        for f in all_files:
            embedded, gps = batch.get(f, (None, None))
            if embedded is None or gps is None:
                continue
            acc = accuracy.get(f, 99.99)
            if acc is None:
                acc = 99.99
            if acc >= 25.0 or acc == 99.99:
                continue
            delta = gps - embedded
            weight = 1.0 / (acc + 1.0)
            pairs.append((delta, weight))

        self.assertGreaterEqual(len(pairs), 1,
                                'Need at least one file with valid GPS fix')

        deltas = [p[0] for p in pairs]
        weights = [p[1] for p in pairs]
        self.median = weighted_median_delta(deltas, weights)
        self.assertIsNotNone(self.median, 'Weighted median delta should be computable')

        print(f'  Weighted median delta: {self.median}')
        print(f'  Based on {len(pairs)} files with valid GPS fix')

        # ── 3. Analyse and build decisions ─────────────────────
        result = an_mod.analyze(self.target)
        self.assertGreater(result.total_files, 0, 'Analysis should find files')

        decisions = {}
        for fs in result.sets:
            decisions[fs.id] = SetDecision(
                strategy=STRATEGY_MANUAL,
                manual_delta=self.median)

        plan = preview.compute_preview(result, decisions, self.median)

        jobs = []
        for pr in plan:
            for fp in pr.file_results:
                jobs.append(WriteJob(
                    path=fp.path,
                    target_embedded=fp.target_embedded,
                    target_mtime=fp.target_mtime,
                ))

        self.assertGreater(len(jobs), 0, 'Should have write jobs')

        # ── 4. Apply corrections via Writer ────────────────────
        fix_btime = 'off'
        if (subprocess.run(['which', 'faketime'], capture_output=True).returncode == 0 and
                subprocess.run(['which', 'mount.exfat-fuse'], capture_output=True).returncode == 0):
            fix_btime = 'auto'
            print('  btime correction enabled (faketime + mount.exfat-fuse available)')
        else:
            print('  btime correction skipped (faketime or mount.exfat-fuse not available)')

        with Writer(self.target, fix_btime=fix_btime,
                    delta=self.median, dry_run=False) as w:
            summary = w.write_all(jobs)

        self.assertEqual(summary.written, len(jobs),
                         f'{summary.written} of {len(jobs)} files should be written')
        errs = summary.errors or []
        if errs:
            print(f'  {len(errs)} write errors: {errs[:3]}')
        print(f'  {summary.written} files corrected')

        # ── 5. Verify metadata changes ─────────────────────────
        after = self._record_metadata(all_files)

        tolerance = timedelta(seconds=2)

        for f in all_files:
            name = f.name
            o = orig[f]
            a = after[f]
            f_median = self.median.total_seconds()

            if o['exif'] is not None and a['exif'] is not None:
                expected = o['exif'] + self.median
                actual_diff = abs((a['exif'] - expected).total_seconds())
                self.assertLess(
                    actual_diff, tolerance.total_seconds(),
                    f'{name} exif: expected {expected}, got {a["exif"]} '
                    f'(diff {actual_diff:.2f}s)')
                print(f'  {name} exif: {o["exif"]} -> {a["exif"]} '
                      f'(Δ={self.median})')

            if a['mtime'] is not None:
                expected = o['mtime'] + self.median
                actual_diff = abs((a['mtime'] - expected).total_seconds())
                self.assertLess(
                    actual_diff, tolerance.total_seconds(),
                    f'{name} mtime: expected {expected}, got {a["mtime"]} '
                    f'(diff {actual_diff:.2f}s)')
                print(f'  {name} mtime: {o["mtime"]} -> {a["mtime"]} '
                      f'(Δ={self.median})')

            if o['btime'] is not None and a['btime'] is not None:
                expected = o['btime'] + self.median
                actual_diff = abs((a['btime'] - expected).total_seconds())
                self.assertLess(
                    actual_diff, tolerance.total_seconds(),
                    f'{name} btime: expected {expected}, got {a["btime"]} '
                    f'(diff {actual_diff:.2f}s)')
                print(f'  {name} btime: {o["btime"]} -> {a["btime"]} '
                      f'(Δ={self.median})')
            elif o['btime'] is not None and fix_btime == 'off':
                print(f'  {name} btime: {o["btime"]} (btime fix skipped)')


if __name__ == '__main__':
    unittest.main()
