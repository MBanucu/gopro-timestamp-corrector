"""Hypothesis: sync() does NOT flush dirty exFAT inodes to the directory entry.

If the kernel exFAT driver's writeback doesn't trigger on sync(),
the dirty inode (mtime=now from exif write) never reaches the
directory entry.  fix_exfat_raw then writes the corrected mtime.
But later (e.g. during verification), something triggers writeback,
overwriting the corrected mtime with now.

Test sequence (parallel-safe: single loop device, single process):
1. Mount exFAT image via sudo mount (not udisksctl)
2. Read raw mtime of a file
3. Touch the file (dirties inode, mtime=now)
4. Call sync()
5. Read raw mtime via loop device — did it change to now?
   If yes: sync() flushed the dirty inode.
   If no: sync() did NOT flush the dirty inode.
6. Call fix_exfat_raw to set mtime to a known target
7. Read raw mtime — should be the target
8. Call os.path.getmtime() (may trigger writeback)
9. Read raw mtime again — should still be the target
"""
import os
import subprocess
import unittest
from datetime import datetime, timezone
from pathlib import Path

_BD = str(Path(__file__).resolve().parent.parent.parent / 'src')
if _BD not in os.environ.get('PYTHONPATH', ''):
    os.environ.setdefault('PYTHONPATH', _BD)

TARGET = 1712345678


class TestSyncFlush(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        gz = Path(__file__).parent.parent / 'sdcard.img.gz'
        if not gz.exists():
            raise unittest.SkipTest('sdcard.img.gz not found')
        from test.shared import decompress_sparse_image, prepare_sparse_image
        import tempfile
        cached = Path(__file__).parent.parent / 'sdcard.img'
        decompress_sparse_image(gz, cached)
        cls._work, cls._img = prepare_sparse_image(gz, prefix='sync_flush_')
        cls._loop = None
        cls._mnt = None
        from test.shared import setup_loop_device, teardown_loop_device
        try:
            cls._loop, cls._mnt = setup_loop_device(str(cls._img))
        except Exception:
            teardown_loop_device(getattr(cls, '_loop', None))
            import shutil
            shutil.rmtree(cls._work, ignore_errors=True)
            raise
        cls._target_dir = Path(cls._mnt) / 'DCIM' / '100GOPRO'
        if not cls._target_dir.exists():
            teardown_loop_device(cls._loop, cls._mnt)
            import shutil
            shutil.rmtree(cls._work, ignore_errors=True)
            raise unittest.SkipTest('100GOPRO not found')
        cls._files = sorted(cls._target_dir.glob('*.MP4'))
        if not cls._files:
            cls._files = sorted(cls._target_dir.glob('*'))

    @classmethod
    def tearDownClass(cls):
        from test.shared import teardown_loop_device
        if cls._loop:
            teardown_loop_device(cls._loop, cls._mnt)
        if cls._work:
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

    def test_does_sync_flush_exfat_inode(self):
        """Step 1-5: touch file, sync, check if raw mtime changed to now."""
        f = self._files[0]
        raw_before = self._raw_mtime(f)
        self.assertIsNotNone(raw_before)
        print(f'  Raw mtime before touch: {raw_before}')

        # Touch the file to dirty inode
        subprocess.run(['touch', '-c', str(f)], capture_output=True)

        raw_after_touch = self._raw_mtime(f)
        print(f'  Raw mtime after touch (no sync): {raw_after_touch}')

        # Sync
        subprocess.run(['sync'])

        raw_after_sync = self._raw_mtime(f)
        print(f'  Raw mtime after sync: {raw_after_sync}')

        # Determine if sync flushed the dirty inode
        if raw_after_sync != raw_before:
            print(f'  *** sync() DID flush mtime={raw_after_sync} to raw block')
        else:
            print(f'  *** sync() did NOT flush mtime to raw block')

    def test_fix_then_getmtime_then_read_raw(self):
        """Full Writer-like sequence: modify → sync → fix → getmtime → read."""
        f = self._files[1] if len(self._files) > 1 else self._files[0]

        # 1. Modify file to dirty inode
        subprocess.run(['touch', '-c', str(f)], capture_output=True)

        # 2. Sync (flush dirty inode)
        subprocess.run(['sync'])

        raw_after_sync = self._raw_mtime(f)
        print(f'  Raw after sync: {raw_after_sync}')

        # 3. Fix mtime
        self._fix_mtime(f, TARGET)
        raw_after_fix = self._raw_mtime(f)
        self.assertEqual(raw_after_fix, TARGET,
                         'Raw mtime should be corrected after fix')
        print(f'  Raw after fix: {raw_after_fix}')

        # 4. Read getmtime (like _record_metadata does)
        stat_mtime = os.path.getmtime(f)
        print(f'  os.path.getmtime(): {int(stat_mtime)}')

        raw_after_stat = self._raw_mtime(f)
        self.assertEqual(raw_after_stat, TARGET,
                         'Raw mtime changed after os.path.getmtime()')
        print(f'  Raw after getmtime: {raw_after_stat}')

        # 5. os.stat (reads btime)
        st = os.stat(f)
        _ = st.st_mtime
        print(f'  os.stat done, birthtime={"present" if hasattr(st, "st_birthtime") and st.st_birthtime else "none"}')

        raw_after_stat2 = self._raw_mtime(f)
        self.assertEqual(raw_after_stat2, TARGET,
                         'Raw mtime changed after os.stat()')
        print(f'  Raw after os.stat: {raw_after_stat2}')

        # 6. Another sync
        subprocess.run(['sync'])

        raw_after_sync2 = self._raw_mtime(f)
        self.assertEqual(raw_after_sync2, TARGET,
                         'Raw mtime changed after final sync()')
        print(f'  Raw after final sync: {raw_after_sync2}')

    def test_fix_without_prior_touch(self):
        """fix_exfat_raw without any prior file modification."""
        f = self._files[2] if len(self._files) > 2 else self._files[0]

        raw_before = self._raw_mtime(f)
        self._fix_mtime(f, TARGET)
        raw_after = self._raw_mtime(f)
        self.assertEqual(raw_after, TARGET,
                         'fix should work without prior modification')
        print(f'  Raw before: {raw_before}  after: {raw_after}')


if __name__ == '__main__':
    unittest.main()
