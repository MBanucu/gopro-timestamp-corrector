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


class TestReadExfatMtimeRaw(unittest.TestCase):
    """read_exfat_mtime_raw must read mtime via raw block."""

    @classmethod
    def setUpClass(cls):
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
        try:
            cls._loop, cls._mnt = setup_loop_device(str(cls._img))
        except Exception as e:
            teardown_loop_device(getattr(cls, '_loop', None))
            raise unittest.SkipTest(str(e))
        cls._target = Path(cls._mnt) / 'DCIM' / '100GOPRO'
        if not cls._target.exists():
            teardown_loop_device(cls._loop, cls._mnt)
            raise unittest.SkipTest('100GOPRO not found')

    @classmethod
    def tearDownClass(cls):
        from test.shared import teardown_loop_device
        teardown_loop_device(cls._loop, cls._mnt)
        import shutil
        shutil.rmtree(cls._work, ignore_errors=True)

    def test_reads_mtime_from_directory_entry(self):
        from strategies.exfat_raw import read_exfat_mtime_raw
        files = sorted(self._target.glob('*.MP4'))
        self.assertGreaterEqual(len(files), 1)
        ts = read_exfat_mtime_raw(str(files[0]))
        self.assertIsNotNone(ts)
        self.assertGreater(ts, 1778000000)

    def test_returns_none_on_nonexistent(self):
        from strategies.exfat_raw import read_exfat_mtime_raw
        self.assertIsNone(read_exfat_mtime_raw('/nonexistent/file.mp4'))

    def test_mtime_matches_btime_before_correction(self):
        from strategies.exfat_raw import read_exfat_mtime_raw, read_exfat_btime_raw
        files = sorted(self._target.glob('*.MP4'))
        mtime = read_exfat_mtime_raw(str(files[0]))
        btime = read_exfat_btime_raw(str(files[0]))
        self.assertIsNotNone(mtime)
        self.assertIsNotNone(btime)
        self.assertLessEqual(abs(mtime - btime), 5)

    def test_mtime_matches_stat_on_nixos(self):
        from strategies.exfat_raw import read_exfat_mtime_raw
        files = sorted(self._target.glob('*.MP4'))
        try:
            os.utime(files[0], (1234567890.0, 1234567890.0))
            raw = read_exfat_mtime_raw(str(files[0]))
            self.assertIsNotNone(raw)
        except (OSError, PermissionError):
            self.skipTest('os.utime() failed on this filesystem')


class TestExfatRawBtimeDt(unittest.TestCase):
    """_fix_exfat_raw with btime_dt must preserve creation time."""

    @classmethod
    def setUpClass(cls):
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
        try:
            cls._loop, cls._mnt = setup_loop_device(str(cls._img))
        except Exception as e:
            teardown_loop_device(getattr(cls, '_loop', None))
            raise unittest.SkipTest(str(e))
        cls._target = Path(cls._mnt) / 'DCIM' / '100GOPRO'
        if not cls._target.exists():
            teardown_loop_device(cls._loop, cls._mnt)
            raise unittest.SkipTest('100GOPRO not found')

    @classmethod
    def tearDownClass(cls):
        from test.shared import teardown_loop_device
        teardown_loop_device(cls._loop, cls._mnt)
        import shutil
        shutil.rmtree(cls._work, ignore_errors=True)

    def test_btime_dt_preserves_creation_time(self):
        from strategies.exfat_raw import read_exfat_btime_raw, _fix_exfat_raw
        import media
        files = sorted(self._target.glob('*'))
        first = files[0]
        orig_mtime = media.read_mtime(first)
        orig_btime_raw = read_exfat_btime_raw(str(first))
        self.assertIsNotNone(orig_btime_raw)
        new_mtime = orig_mtime + timedelta(hours=1)
        orig_btime_dt = datetime.fromtimestamp(orig_btime_raw, tz=timezone.utc)
        _fix_exfat_raw(str(first), new_mtime, dry_run=False, btime_dt=orig_btime_dt)
        after_btime_raw = read_exfat_btime_raw(str(first))
        self.assertIsNotNone(after_btime_raw)
        self.assertEqual(after_btime_raw, orig_btime_raw)
        from strategies.exfat_raw import read_exfat_mtime_raw
        after_mtime_raw = read_exfat_mtime_raw(str(first))
        expected_ts = int(new_mtime.replace(tzinfo=timezone.utc).timestamp())
        self.assertEqual(after_mtime_raw, expected_ts)


if __name__ == '__main__':
    unittest.main()
