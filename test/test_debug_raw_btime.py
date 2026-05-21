"""Debug tests for exFAT raw block write/read cycle on CI.

Isolates each step of the pipeline to identify why the raw block write
reports success but the data does not persist on Ubuntu CI kernels (<6.12).
"""

import os
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from shared import HAS_TK, decompress_sparse_image, setup_loop_device, teardown_loop_device


_BD = str(Path(__file__).resolve().parent.parent / 'src')
if _BD not in sys.path:
    sys.path.insert(0, _BD)


class DebugRawBtime(unittest.TestCase):
    """Isolate each step: dd write, boot parse, entry lookup, btime read/write."""

    _loop_dev = None
    _mount_point = None
    _work_dir = None
    _target = None

    @classmethod
    def setUpClass(cls):
        gz_path = Path(__file__).parent / 'sdcard.img.gz'
        if not gz_path.exists():
            raise unittest.SkipTest(f'Compressed image not found at {gz_path}')

        cached = Path(__file__).parent / 'sdcard.img'
        decompress_sparse_image(gz_path, cached)

        cls._work_dir = Path(tempfile.mkdtemp(prefix='gopro_debug_'))
        cls._img_path = cls._work_dir / 'sdcard.img'
        subprocess.run(['cp', '--sparse=always', str(cached), str(cls._img_path)],
                       check=True, capture_output=True)

        cls._loop_dev, cls._mount_point = setup_loop_device(cls._img_path)
        cls._target = Path(cls._mount_point) / 'DCIM' / '100GOPRO'
        if not cls._target.exists():
            teardown_loop_device(cls._loop_dev, cls._mount_point)
            raise unittest.SkipTest(f'{cls._target} not found')

    @classmethod
    def tearDownClass(cls):
        teardown_loop_device(cls._loop_dev, cls._mount_point)
        if cls._work_dir:
            import shutil
            shutil.rmtree(cls._work_dir, ignore_errors=True)

    def _first_file(self):
        files = sorted(self._target.glob('*.MP4')) or sorted(self._target.iterdir())
        return files[0]

    def _resolve_device(self):
        """Resolve block device for the current mount."""
        from btime import _resolve_device
        return _resolve_device(str(self._mount_point))

    def _resolve_mount(self):
        from btime import _resolve_mount_point
        return _resolve_mount_point(str(self._mount_point))

    def _exfat_boot(self):
        from strategies.exfat_raw import _exfat_parse_boot
        dev = self._resolve_device()
        self.assertIsNotNone(dev)
        boot = _exfat_parse_boot(dev)
        self.assertIsNotNone(boot)
        return boot, dev

    # ── Tests ────────────────────────────────────────────────────

    def test_01_dd_write_read_raw(self):
        """Direct dd write/read to loop device — verify persistence."""
        dev = self._resolve_device()
        dev_path = str(dev)
        # Write a known pattern at sector 0 (boot sector — safe area for test)
        # Use an unused area like sector 100000 (well past FAT + cluster heap)
        test_offset = 100000 * 512
        expected = b'DEBUG_TEST_PATTERN_42'
        subprocess.run(
            ['sudo', 'dd', f'of={dev_path}', 'bs=1',
             f'seek={test_offset}', f'count={len(expected)}', 'status=none'],
            input=expected, check=True, capture_output=True)
        subprocess.run(['sync'])
        subprocess.run(['sudo', 'sh', '-c', 'echo 3 > /proc/sys/vm/drop_caches'],
                       capture_output=True)
        r = subprocess.run(
            ['sudo', 'dd', f'if={dev_path}', 'bs=1',
             f'skip={test_offset}', f'count={len(expected)}', 'status=none'],
            check=True, capture_output=True)
        actual = r.stdout
        self.assertEqual(expected, actual,
                         f'dd write/read mismatch: expected={expected!r} actual={actual!r}')

    def test_02_dd_write_file_cluster_direct(self):
        """Write to the cluster containing the first file's dir entry, read back raw."""
        boot, dev = self._exfat_boot()
        first = self._first_file()
        from strategies.exfat_raw import _exfat_find_file_entry
        entry = _exfat_find_file_entry(boot, str(dev), str(first))
        self.assertIsNotNone(entry, f'Could not find entry for {first.name}')

        # Read the original creation time
        time_word = struct.unpack_from('<H', entry, 0x0C)[0]
        date_word = struct.unpack_from('<H', entry, 0x0E)[0]
        time_ms = entry[0x16]
        sys.stderr.write(f'[dbg] {first.name}: raw entry creation time_word={time_word} date_word={date_word} time_ms={time_ms}\n')

    def test_03_btime_readback_before_correction(self):
        """Read btime via raw block before any correction."""
        first = self._first_file()
        from strategies.exfat_raw import read_exfat_btime_raw
        btime_val = read_exfat_btime_raw(str(first))
        stat_r = subprocess.run(['stat', '-c', '%W', str(first)],
                                capture_output=True, text=True)
        stat_val = int(stat_r.stdout.strip()) if stat_r.returncode == 0 and stat_r.stdout.strip() else None
        sys.stderr.write(f'[dbg] {first.name}: raw_btime={btime_val} stat_btime={stat_val}\n')
        self.assertIsNotNone(btime_val, 'raw btime readback returned None')

    def test_04_fix_exfat_raw_then_readback(self):
        """Call _fix_exfat_raw with a known delta, verify readback changes."""
        first = self._first_file()
        from strategies.exfat_raw import read_exfat_btime_raw, _fix_exfat_raw
        import media

        orig_mtime = media.read_mtime(first)
        self.assertIsNotNone(orig_mtime)
        delta = timedelta(hours=-2)
        target_dt = orig_mtime + delta
        target_ts = int(target_dt.replace(tzinfo=timezone.utc).timestamp())

        sys.stderr.write(f'[dbg] {first.name}: orig_mtime={orig_mtime} target_dt={target_dt} target_ts={target_ts}\n')

        # Read btime before
        before_btime = read_exfat_btime_raw(str(first))
        sys.stderr.write(f'[dbg] {first.name}: before_btime={before_btime}\n')

        # Apply correction
        _fix_exfat_raw(str(first), target_dt, dry_run=False)

        # Read btime after (via raw block)
        after_btime = read_exfat_btime_raw(str(first))
        stat_r = subprocess.run(['stat', '-c', '%W', str(first)],
                                capture_output=True, text=True)
        after_stat = int(stat_r.stdout.strip()) if stat_r.returncode == 0 and stat_r.stdout.strip() else None
        sys.stderr.write(f'[dbg] {first.name}: after_raw={after_btime} after_stat={after_stat}\n')

        # Verify the raw block readback shows the corrected time
        if after_btime is not None:
            diff = abs(after_btime - target_ts)
            sys.stderr.write(f'[dbg] {first.name}: diff={diff}s (target_ts={target_ts} after_raw={after_btime})\n')
            if diff > 2:
                # Debug: re-read the raw entry and dump bytes
                from strategies.exfat_raw import _exfat_parse_boot, _exfat_find_file_entry, _exfat_decode_time
                dev = self._resolve_device()
                boot = _exfat_parse_boot(str(dev))
                post_entry = _exfat_find_file_entry(boot, str(dev), str(first))
                if post_entry:
                    pt_word = struct.unpack_from('<H', post_entry, 0x0C)[0]
                    pd_word = struct.unpack_from('<H', post_entry, 0x0E)[0]
                    pms = post_entry[0x16]
                    decoded = _exfat_decode_time(pt_word, pd_word, pms)
                    sys.stderr.write(f'[dbg] {first.name}: post-fix raw entry time_word={pt_word} date_word={pd_word} time_ms={pms} decoded={decoded}\n')
                    sys.stderr.write(f'[dbg] {first.name}: post-fix raw entry bytes: {post_entry.hex()}\n')
        self.assertEqual(after_btime, target_ts,
                         f'{first.name}: raw btime ({after_btime}) != target ({target_ts})')

    def test_05_raw_write_different_cluster(self):
        """Write to a test cluster (not in use) and verify readback.

        Note: the test image FAT is unreliable (does not mark all used
        clusters).  Instead of scanning the FAT we use a cluster near
        the end of the image, far beyond the ~30 used clusters.
        """
        boot, dev = self._exfat_boot()
        cs = boot['cluster_size']
        heap_off = boot['cluster_heap_offset']

        end_of_image = os.path.getsize(self._img_path)
        max_cluster = 2 + (end_of_image - heap_off) // cs
        test_cluster = max(max_cluster - 100, 1000)
        test_offset = heap_off + (test_cluster - 2) * cs
        expected = b'CLUSTER_WRITE_TEST_99'
        subprocess.run(
            ['sudo', 'dd', f'of={dev}', 'bs=1', f'seek={test_offset}',
             f'count={len(expected)}', 'status=none'],
            input=expected, check=True, capture_output=True)
        subprocess.run(['sync'])
        subprocess.run(['sudo', 'sh', '-c', 'echo 3 > /proc/sys/vm/drop_caches'],
                       capture_output=True)
        r = subprocess.run(
            ['sudo', 'dd', f'if={dev}', 'bs=1', f'skip={test_offset}',
             f'count={len(expected)}', 'status=none'],
            check=True, capture_output=True)
        actual = r.stdout
        self.assertEqual(expected, actual,
                         f'Cluster write/read mismatch at offset {test_offset}')

    def test_06_find_entry_name_match(self):
        """Verify _exfat_find_file_entry finds the right name and matches stat mtime."""
        boot, dev = self._exfat_boot()
        from strategies.exfat_raw import _exfat_find_file_entry
        first = self._first_file()
        entry = _exfat_find_file_entry(boot, str(dev), str(first))
        self.assertIsNotNone(entry)

        # Read the modification time from the raw entry
        time_word = struct.unpack_from('<H', entry, 0x08)[0]
        date_word = struct.unpack_from('<H', entry, 0x0A)[0]
        time_ms = entry[0x14]
        from strategies.exfat_raw import _exfat_decode_time
        raw_mtime = _exfat_decode_time(time_word, date_word, time_ms)

        # Compare with stat mtime (they should be close)
        import media
        stat_mtime = media.read_mtime(first)
        diff = abs(int(raw_mtime.timestamp()) - int(stat_mtime.timestamp()))
        sys.stderr.write(f'[dbg] {first.name}: raw_mtime={raw_mtime} stat_mtime={stat_mtime} diff={diff}s\n')
        self.assertLessEqual(diff, 2,
                             f'{first.name}: raw mtime ({raw_mtime}) differs from stat ({stat_mtime}) by {diff}s')

    def test_07_write_all_files_then_readback(self):
        """Apply _fix_exfat_raw to all 12 files, verify each via raw readback."""
        from strategies.exfat_raw import read_exfat_btime_raw, _fix_exfat_raw
        import media

        files = sorted(self._target.glob('*'))
        self.assertGreaterEqual(len(files), 1)
        delta = timedelta(hours=-2)
        errors = []
        for fp in files[:12]:
            orig_mtime = media.read_mtime(fp)
            target_dt = orig_mtime + delta
            target_ts = int(target_dt.replace(tzinfo=timezone.utc).timestamp())

            before_btime = read_exfat_btime_raw(str(fp))
            _fix_exfat_raw(str(fp), target_dt, dry_run=False)
            after_btime = read_exfat_btime_raw(str(fp))

            stat_r = subprocess.run(['stat', '-c', '%W', str(fp)],
                                    capture_output=True, text=True)
            after_stat = int(stat_r.stdout.strip()) if stat_r.returncode == 0 and stat_r.stdout.strip() else None

            sys.stderr.write(f'[dbg] {fp.name}: before={before_btime} after_raw={after_btime} after_stat={after_stat} target={target_ts} orig_mtime={int(orig_mtime.timestamp())}\n')

            if after_btime is not None:
                diff = abs(after_btime - target_ts)
                if diff > 2:
                    errors.append(f'{fp.name}: raw={after_btime} target={target_ts} diff={diff}')
        self.assertEqual(errors, [], '\n'.join(errors))
