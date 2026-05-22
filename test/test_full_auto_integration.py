"""Full end-to-end integration test: auto calibrate, apply all, verify metadata."""
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


class TestFullAutoIntegration(unittest.TestCase):
    """Copy sdcard image, mount, auto calibrate, apply all, verify every metadata field."""

    @classmethod
    def setUpClass(cls):
        # Debug: show timezone info that affects this process
        import time as _time
        tz = os.environ.get('TZ', '(unset)')
        print(f'[tzdiag] TZ={tz!r} time.tzname={_time.tzname!r} '
              f'time.timezone={_time.timezone} is_dst={_time.daylight}')
        import datetime as _dt
        print(f'[tzdiag] datetime.now()={_dt.datetime.now()!r} '
              f'datetime.now(utc)={_dt.datetime.now(_dt.timezone.utc)!r}')

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

        from shared import decompress_sparse_image, setup_loop_device

        # Decompress to the test directory (cached — skip if already present)
        cached = Path(__file__).parent / 'sdcard.img'
        decompress_sparse_image(gz_path, cached)

        # Copy to a temp directory so we never modify the cached image
        cls._work_dir = Path(tempfile.mkdtemp(prefix='gopro_full_test_'))
        cls.img_path = cls._work_dir / 'sdcard.img'
        subprocess.run(['cp', '--sparse=always', str(cached), str(cls.img_path)],
                       check=True, capture_output=True)

        cls.loop_dev, cls.mount_point = setup_loop_device(cls.img_path)

        cls.target = Path(cls.mount_point) / 'DCIM' / '100GOPRO'
        if not cls.target.exists():
            raise unittest.SkipTest(f'{cls.target} not found')

        # Register cleanup early so tearDown always runs
        cls._temp_dir = cls._work_dir

    @classmethod
    def tearDownClass(cls):
        from shared import teardown_loop_device
        teardown_loop_device(cls.loop_dev, cls.mount_point)
        if cls._work_dir:
            shutil.rmtree(cls._work_dir, ignore_errors=True)

    # ── Helpers ───────────────────────────────────────────────

    def _read_exif_batch(self, paths: list[Path]) -> dict[Path, datetime | None]:
        """Read QuickTime:CreateDate for all *paths* in a single exiftool call."""
        if not paths:
            return {}
        r = subprocess.run(
            ['exiftool', '-json', '-QuickTime:CreateDate'] + [str(p) for p in paths],
            capture_output=True, text=True)
        if r.returncode != 0 or not r.stdout.strip():
            return {}
        import json as _json
        from exiftool_session import _parse_dt
        out = {}
        for rec in _json.loads(r.stdout):
            src = rec.get('SourceFile')
            raw = rec.get('CreateDate')
            if not src or not raw:
                continue
            dt = _parse_dt(str(raw))
            if dt is not None:
                out[Path(src)] = dt
            else:
                out[Path(src)] = None
        return out

    def _read_mtime(self, path):
        try:
            ts = os.path.getmtime(path)
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except OSError:
            return None

    def _read_btime(self, path):
        st = os.stat(path)
        bt = getattr(st, 'st_birthtime', None)
        if bt is not None:
            return datetime.fromtimestamp(bt, tz=timezone.utc)
        return None

    def _record_metadata(self, files):
        exif_batch = self._read_exif_batch(files)
        md = {}
        for f in files:
            mtime = self._read_mtime(f)
            if mtime is not None:
                import os as _os
                raw_ts = _os.path.getmtime(f)
                print(f'[tzdiag] {f.name}: os.path.getmtime()={raw_ts} -> {mtime}')
            md[f] = {
                'exif': exif_batch.get(f),
                'mtime': mtime,
                'btime': self._read_btime(f),
            }
        return md

    # ── Tests ─────────────────────────────────────────────────

    def test_full_pipeline(self):
        """Auto calibrate, apply all, verify every metadata field changed by the median delta."""
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

        from exiftool_session import ExifToolSession
        import media
        import analysis as an_mod
        import preview
        from preview import STRATEGY_MANUAL, SetDecision
        from resolve import weighted_median_delta
        from writer import Writer, WriteJob

        all_files = media.collect(self.target)
        t_total = time.perf_counter()

        with ExifToolSession() as session:
            # ── 1. Record original metadata ────────────────────────
            t0 = time.perf_counter()
            orig = self._record_metadata(all_files)
            t_meta = time.perf_counter() - t0

            # ── 2. Compute weighted median delta (auto calibration) ─
            t0 = time.perf_counter()
            batch = session.read_tags_batch(all_files)
            accuracy = session.read_gps_accuracy_batch(all_files)
            t_batch = time.perf_counter() - t0

            t0 = time.perf_counter()
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
            t_auto = time.perf_counter() - t0

            print(f'  Weighted median delta: {self.median}')
            print(f'  Based on {len(pairs)} files with valid GPS fix')

            # ── 3. Analyse and build decisions ─────────────────────
            t0 = time.perf_counter()
            result = an_mod.analyze(session, self.target)
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
            t_plan = time.perf_counter() - t0

            # ── 4. Apply corrections via Writer ────────────────────
            t0 = time.perf_counter()
            fix_btime = 'off'
            if (subprocess.run(['which', 'faketime'], capture_output=True).returncode == 0 and
                    subprocess.run(['which', 'mount.exfat-fuse'], capture_output=True).returncode == 0):
                fix_btime = 'auto'
                print('  btime correction enabled (faketime + mount.exfat-fuse available)')
            else:
                print('  btime correction skipped (faketime or mount.exfat-fuse not available)')

            with Writer(self.target, fix_btime=fix_btime,
                        delta=self.median, dry_run=False, session=session) as w:
                summary = w.write_all(jobs)

        self.assertEqual(summary.written, len(jobs),
                         f'{summary.written} of {len(jobs)} files should be written')
        errs = summary.errors or []
        if errs:
            print(f'  {len(errs)} write errors: {errs[:3]}')
        print(f'  {summary.written} files corrected')
        t_write = time.perf_counter() - t0

        # ── 5. Verify metadata changes ─────────────────────────
        t0 = time.perf_counter()
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

            expected_mtime = o['mtime'] + self.median if o['mtime'] else None
            if expected_mtime is not None:
                mtime_ok = False
                if a['mtime'] is not None:
                    diff = abs((a['mtime'] - expected_mtime).total_seconds())
                    if diff <= tolerance.total_seconds():
                        mtime_ok = True
                        print(f'  {name} mtime: {o["mtime"]} -> {a["mtime"]} '
                              f'(Δ={self.median})')
                if not mtime_ok:
                    from strategies.exfat_raw import exfat_ops
                    raw_ts = exfat_ops.read_mtime_raw(str(f))
                    if raw_ts is not None:
                        raw_dt = datetime.fromtimestamp(raw_ts, tz=timezone.utc)
                        raw_diff = abs((raw_dt - expected_mtime).total_seconds())
                        if raw_diff <= tolerance.total_seconds():
                            mtime_ok = True
                            print(f'  {name} mtime: {o["mtime"]} -> {raw_dt} '
                                  f'(raw block, kernel cache stale, Δ={self.median})')
                        else:
                            print(f'  {name} mtime raw MISMATCH: '
                                  f'raw={raw_dt} expected={expected_mtime} '
                                  f'diff={raw_diff:.1f}s')
                    else:
                        print(f'  {name} mtime raw: None (read_mtime_raw failed)')
                if not mtime_ok:
                    actual = a['mtime'] if a['mtime'] else 'N/A'
                    self.fail(f'{name} mtime: expected {expected_mtime}, got {actual}')

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

        t_verify = time.perf_counter() - t0
        t_total = time.perf_counter() - t_total
        print(f'\n  TIMING')
        print(f'  │ record metadata:     {t_meta:.2f}s')
        print(f'  │ batch exiftool:      {t_batch:.2f}s')
        print(f'  │ auto calibrate:      {t_auto:.2f}s')
        print(f'  │ build plan:          {t_plan:.2f}s')
        print(f'  │ write corrections:   {t_write:.2f}s')
        print(f'  │ verify metadata:     {t_verify:.2f}s')
        print(f'  │ {"─" * 30}')
        print(f'  │ total:               {t_total:.2f}s')

if __name__ == '__main__':
    unittest.main()
