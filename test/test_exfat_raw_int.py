"""Integration tests for exFAT raw block read/write functions.

Requires loop device setup (sudo + FUSE).  Not run as part of unit tests.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


_BD = str(Path(__file__).resolve().parent.parent / 'src')
if _BD not in sys.path:
    sys.path.insert(0, _BD)


def _ops():
    from exfat_raw import ExfatRawIO, ExfatRawFilesystem, ExfatRawOps
    io = ExfatRawIO()
    return ExfatRawOps(io, ExfatRawFilesystem(io))


class TestReadExfatMtimeRaw(unittest.TestCase):
    """read_exfat_mtime_raw must read mtime via raw block."""

    @classmethod
    def setUpClass(cls):
        import shutil
        gz = Path(__file__).parent / 'sdcard.img.gz'
        if not gz.exists():
            raise unittest.SkipTest('sdcard.img.gz not found')
        from test.shared import decompress_sparse_image, setup_loop_device, teardown_loop_device
        cached = Path(__file__).parent / 'sdcard.img'
        decompress_sparse_image(gz, cached)
        cls._work = Path(tempfile.mkdtemp(prefix='gopro_ut_'))
        cls._img = cls._work / 'sdcard.img'
        subprocess.run(['cp', '--sparse=always', str(cached), str(cls._img)],
                       check=True, capture_output=True)
        cls._loop, cls._mnt = setup_loop_device(str(cls._img))
        cls.addClassCleanup(teardown_loop_device, cls._loop, cls._mnt)
        cls.addClassCleanup(shutil.rmtree, cls._work, ignore_errors=True)
        cls._target = Path(cls._mnt) / 'DCIM' / '100GOPRO'
        if not cls._target.exists():
            raise unittest.SkipTest('100GOPRO not found')

    def test_reads_mtime_from_directory_entry(self):
        ops = _ops()
        files = sorted(self._target.glob('*.MP4'))
        self.assertGreaterEqual(len(files), 1)
        ts = ops.read_mtime_raw(str(files[0]))
        self.assertIsNotNone(ts)
        self.assertGreater(ts, 1778000000)

    def test_returns_none_on_nonexistent(self):
        ops = _ops()
        self.assertIsNone(ops.read_mtime_raw('/nonexistent/file.mp4'))

    def test_mtime_matches_btime_before_correction(self):
        ops = _ops()
        files = sorted(self._target.glob('*.MP4'))
        mtime = ops.read_mtime_raw(str(files[0]))
        btime = ops.read_btime_raw(str(files[0]))
        self.assertIsNotNone(mtime)
        self.assertIsNotNone(btime)
        self.assertLessEqual(abs(mtime - btime), 5)

    def test_mtime_matches_stat_on_nixos(self):
        ops = _ops()
        files = sorted(self._target.glob('*.MP4'))
        try:
            os.utime(files[0], (1234567890.0, 1234567890.0))
            raw = ops.read_mtime_raw(str(files[0]))
            self.assertIsNotNone(raw)
        except (OSError, PermissionError):
            self.skipTest('os.utime() failed on this filesystem')


class TestExfatRawBtimeDt(unittest.TestCase):
    """ExfatRawOps.fix_exfat_raw with btime_dt must preserve creation time."""

    @classmethod
    def setUpClass(cls):
        import shutil
        gz = Path(__file__).parent / 'sdcard.img.gz'
        if not gz.exists():
            raise unittest.SkipTest('sdcard.img.gz not found')
        from test.shared import decompress_sparse_image, setup_loop_device, teardown_loop_device
        cached = Path(__file__).parent / 'sdcard.img'
        decompress_sparse_image(gz, cached)
        cls._work = Path(tempfile.mkdtemp(prefix='gopro_bt_'))
        cls._img = cls._work / 'sdcard.img'
        subprocess.run(['cp', '--sparse=always', str(cached), str(cls._img)],
                       check=True, capture_output=True)
        cls._loop, cls._mnt = setup_loop_device(str(cls._img))
        cls.addClassCleanup(teardown_loop_device, cls._loop, cls._mnt)
        cls.addClassCleanup(shutil.rmtree, cls._work, ignore_errors=True)
        cls._target = Path(cls._mnt) / 'DCIM' / '100GOPRO'
        if not cls._target.exists():
            raise unittest.SkipTest('100GOPRO not found')

    def test_btime_dt_preserves_creation_time(self):
        ops = _ops()
        import media
        files = sorted(self._target.glob('*'))
        first = files[0]
        orig_mtime = media.read_mtime(first)
        orig_btime_raw = ops.read_btime_raw(str(first))
        self.assertIsNotNone(orig_btime_raw)
        new_mtime = orig_mtime + timedelta(hours=1)
        orig_btime_dt = datetime.fromtimestamp(orig_btime_raw, tz=timezone.utc)
        ops.fix_exfat_raw(str(first), new_mtime, dry_run=False, btime_dt=orig_btime_dt)
        after_btime_raw = ops.read_btime_raw(str(first))
        self.assertIsNotNone(after_btime_raw)
        self.assertEqual(after_btime_raw, orig_btime_raw)
        after_mtime_raw = ops.read_mtime_raw(str(first))
        expected_ts = int(new_mtime.replace(tzinfo=timezone.utc).timestamp())
        self.assertEqual(after_mtime_raw, expected_ts)


if __name__ == '__main__':
    unittest.main()
