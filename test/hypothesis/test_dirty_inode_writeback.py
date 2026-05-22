"""Hypothesis: dirty inode writeback overwrites corrected directory entry.

When a file is modified (e.g., exif metadata write), the kernel marks
the inode dirty with mtime = now.  If the exFAT driver subsequently
syncs this dirty inode, it WRITES the in-memory mtime (= now) to the
directory entry, overwriting any corrected value set by
``fix_exfat_raw``.

Test sequence:
1. Mount an exFAT image
2. Touch a file (dirties inode, mtime = now)
3. Call ``fix_exfat_raw`` to set mtime to a known value
4. Read back raw mtime — should be the corrected value
5. Call ``os.stat()`` or ``os.path.getmtime()`` on the file
   (may trigger exFAT driver writeback)
6. Read back raw mtime again — if step 5 triggered writeback,
   the mtime will have reverted to ``now``.
"""
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

_BD = str(Path(__file__).resolve().parent.parent.parent / 'src')
if _BD not in os.environ.get('PYTHONPATH', ''):
    os.environ.setdefault('PYTHONPATH', _BD)


class TestDirtyInodeWriteback(unittest.TestCase):

    TARGET_TS = 1712345678  # known fixed timestamp

    @classmethod
    def setUpClass(cls):
        from test.shared import decompress_sparse_image, prepare_sparse_image, \
            setup_loop_device, teardown_loop_device
        gz = Path(__file__).parent.parent / 'sdcard.img.gz'
        if not gz.exists():
            raise unittest.SkipTest('sdcard.img.gz not found')
        cached = Path(__file__).parent.parent / 'sdcard.img'
        decompress_sparse_image(gz, cached)
        cls._work, cls._img = prepare_sparse_image(gz, prefix='dirty_inode_')
        try:
            cls._loop, cls._mnt = setup_loop_device(str(cls._img))
        except Exception:
            teardown_loop_device(getattr(cls, '_loop', None))
            import shutil
            shutil.rmtree(cls._work, ignore_errors=True)
            raise
        cls.target = Path(cls._mnt) / 'DCIM' / '100GOPRO'
        if not cls.target.exists():
            teardown_loop_device(cls._loop, cls._mnt)
            shutil.rmtree(cls._work, ignore_errors=True)
            raise unittest.SkipTest('100GOPRO not found')
        cls.files = sorted(cls.target.glob('*.MP4'))
        if not cls.files:
            cls.files = sorted(cls.target.glob('*'))

    @classmethod
    def tearDownClass(cls):
        from test.shared import teardown_loop_device
        teardown_loop_device(cls._loop, cls._mnt)
        import shutil
        shutil.rmtree(cls._work, ignore_errors=True)

    def _raw_mtime(self, path):
        from strategies.exfat_raw import ExfatRawIO, ExfatRawFilesystem, ExfatRawOps
        io = ExfatRawIO()
        fs = ExfatRawFilesystem(io)
        ops = ExfatRawOps(io, fs)
        return ops.read_mtime_raw(str(path))

    def _fix_mtime(self, path, ts):
        from strategies.exfat_raw import ExfatRawIO, ExfatRawFilesystem, ExfatRawOps
        io = ExfatRawIO()
        fs = ExfatRawFilesystem(io)
        ops = ExfatRawOps(io, fs)
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        ops.fix_exfat_raw(str(path), dt, dry_run=False)

    def test_fix_then_getmtime_preserves_raw_mtime(self):
        """fix_exfat_raw + os.path.getmtime should not change raw mtime."""
        f = self.files[0]
        orig_raw = self._raw_mtime(f)
        self.assertIsNotNone(orig_raw, 'raw mtime should be readable before fix')

        # Step 1: modify file to dirty inode
        subprocess.run(['touch', '-c', str(f)], capture_output=True)

        # Step 2: fix mtime via raw block
        self._fix_mtime(f, self.TARGET_TS)
        after_fix = self._raw_mtime(f)
        self.assertEqual(after_fix, self.TARGET_TS,
                         'raw mtime should be corrected after fix_exfat_raw')

        # Step 3: read stat (may trigger writeback of dirty inode)
        # Read btime via stat to force exFAT driver to access directory entry
        st = os.stat(f)
        _ = st.st_mtime
        _ = getattr(st, 'st_birthtime', 0)

        # Step 4: read raw mtime again
        after_stat = self._raw_mtime(f)
        self.assertEqual(
            after_stat, self.TARGET_TS,
            f'raw mtime changed from {after_fix} to {after_stat} '
            f'after os.stat() — writeback may have overwritten it')

    def test_fix_after_exif_write_preserves_raw_mtime(self):
        """Simulate Writer flow: exif metadata write → fix_exfat_raw.

        The exif write dirties the inode. After fix_exfat_raw,
        the raw mtime must stay corrected.
        """
        f = self.files[0]

        # Simulate exif metadata write by touching the file
        subprocess.run(['touch', '-c', str(f)], capture_output=True)
        subprocess.run(['sync'])  # flush dirty inode before fix

        # Fix mtime
        self._fix_mtime(f, self.TARGET_TS)
        after_fix = self._raw_mtime(f)
        self.assertEqual(after_fix, self.TARGET_TS,
                         'raw mtime should be corrected after fix_exfat_raw')

        # Record metadata (as done in test_full_auto_integration)
        _ = os.path.getmtime(f)
        _ = os.stat(f)

        # Check raw mtime hasn't changed
        after_record = self._raw_mtime(f)
        self.assertEqual(
            after_record, self.TARGET_TS,
            f'raw mtime changed from {after_fix} to {after_record} '
            f'after metadata recording')

    def test_fix_then_stat_st_birthtime_preserves_raw(self):
        """Reading st_birthtime via stat may trigger exFAT driver to
        read the directory entry, potentially causing a writeback."""
        f = self.files[0]

        # Fix mtime
        self._fix_mtime(f, self.TARGET_TS)
        after_fix = self._raw_mtime(f)
        self.assertEqual(after_fix, self.TARGET_TS)

        # Read btime via os.stat
        st = os.stat(f)
        bt = getattr(st, 'st_birthtime', None)
        self.assertIsNotNone(bt, 'st_birthtime should exist')

        # Check raw mtime
        after_stat = self._raw_mtime(f)
        self.assertEqual(
            after_stat, self.TARGET_TS,
            f'raw mtime changed after os.stat() — btime read may '
            f'have triggered writeback')

    def test_fix_after_open_write_truncate(self):
        """Opening the file for write (truncate) dirties the inode."""
        f = self.files[0]

        # Open for append (modifies file, dirties inode)
        fd = os.open(str(f), os.O_WRONLY | os.O_APPEND)
        os.close(fd)
        subprocess.run(['sync'])

        # Fix mtime
        self._fix_mtime(f, self.TARGET_TS)
        after_fix = self._raw_mtime(f)
        self.assertEqual(after_fix, self.TARGET_TS)

        # Record metadata
        _ = os.path.getmtime(f)
        _ = os.stat(f)

        # Check raw mtime
        after_record = self._raw_mtime(f)
        self.assertEqual(
            after_record, self.TARGET_TS,
            'raw mtime changed after metadata recording')

    def test_parallel_fix_isolation(self):
        """Two parallel fix_exfat_raw calls on different files must not
        interfere with each other's directory entries.

        This simulates the parallel timezone test execution.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        f_a = self.files[0]
        f_b = self.files[1] if len(self.files) > 1 else f_a

        def fix_file(f, ts):
            self._fix_mtime(f, ts)
            raw = self._raw_mtime(f)
            return (str(f.name), raw, ts)

        ts_a = self.TARGET_TS
        ts_b = self.TARGET_TS + 3600  # 1 hour later

        with ThreadPoolExecutor(max_workers=2) as pool:
            futs = {
                pool.submit(fix_file, f_a, ts_a): 'A',
                pool.submit(fix_file, f_b, ts_b): 'B',
            }
            for fut in as_completed(futs):
                name, raw, expected = fut.result()
                self.assertEqual(
                    raw, expected,
                    f'{name}: parallel fix_exfat_raw returned {raw}, '
                    f'expected {expected}')


if __name__ == '__main__':
    unittest.main()
