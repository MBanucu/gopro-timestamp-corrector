"""Integration test for exFAT raw block btime strategy."""
import ctypes
import ctypes.util
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


class statx_timestamp(ctypes.Structure):
    _fields_ = [
        ('tv_sec', ctypes.c_int64),
        ('tv_nsec', ctypes.c_uint32),
        ('__reserved', ctypes.c_int32),
    ]


class statx_buf(ctypes.Structure):
    _fields_ = [
        ('stx_mask', ctypes.c_uint32),
        ('stx_blksize', ctypes.c_uint32),
        ('stx_attributes', ctypes.c_uint64),
        ('stx_nlink', ctypes.c_uint32),
        ('stx_uid', ctypes.c_uint32),
        ('stx_gid', ctypes.c_uint32),
        ('stx_mode', ctypes.c_uint16),
        ('__spare0', ctypes.c_uint16 * 1),
        ('stx_ino', ctypes.c_uint64),
        ('stx_size', ctypes.c_uint64),
        ('stx_blocks', ctypes.c_uint64),
        ('stx_attributes_mask', ctypes.c_uint64),
        ('stx_atime', statx_timestamp),
        ('stx_btime', statx_timestamp),
        ('stx_ctime', statx_timestamp),
        ('stx_mtime', statx_timestamp),
        ('stx_rdev_major', ctypes.c_uint32),
        ('stx_rdev_minor', ctypes.c_uint32),
        ('stx_dev_major', ctypes.c_uint32),
        ('stx_dev_minor', ctypes.c_uint32),
        ('__spare2', ctypes.c_uint64 * 14),
    ]


STATX_BTIME = 0x00000200
_libc = ctypes.CDLL(ctypes.util.find_library('c'), use_errno=True)
_libc.statx.restype = ctypes.c_int
_libc.statx.argtypes = [
    ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_uint,
    ctypes.POINTER(statx_buf),
]


def read_btime(path: str) -> int | None:
    buf = statx_buf()
    ret = _libc.statx(-100, path.encode(), 0, STATX_BTIME, ctypes.byref(buf))
    if ret == 0 and (buf.stx_mask & STATX_BTIME):
        return buf.stx_btime.tv_sec
    return None


@unittest.skipIf(
    not shutil.which('udisksctl'),
    'udisksctl not available')
@unittest.skipIf(
    not shutil.which('sudo'),
    'sudo not available')
class TestExfatRawBtime(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        gz_path = Path(__file__).parent / 'sdcard.img.gz'
        if not gz_path.exists():
            raise unittest.SkipTest('sdcard.img.gz not found')

        from test.shared import decompress_sparse_image
        cached = Path(__file__).parent / 'sdcard.img'
        decompress_sparse_image(gz_path, cached)

        cls._work_dir = Path(tempfile.mkdtemp(prefix='gopro_raw_test_'))
        cls.img_path = cls._work_dir / 'sdcard.img'
        subprocess.run(['cp', '--sparse=always', str(cached), str(cls.img_path)],
                       check=True, capture_output=True)

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
        if r.returncode == 0:
            m = re.search(r'at ([^ \n]+)', r.stdout)
            cls.mount_point = m.group(1).rstrip('.') if m else None
        elif 'AlreadyMounted' in (r.stderr or ''):
            m = re.search(r"at `([^`]+)'", r.stderr)
            cls.mount_point = m.group(1) if m else None
        if not cls.mount_point:
            raise unittest.SkipTest('udisksctl mount failed')

        cls.target = Path(cls.mount_point) / 'DCIM' / '100GOPRO'
        if not cls.target.exists():
            raise unittest.SkipTest(f'{cls.target} not found')

    @classmethod
    def tearDownClass(cls):
        if cls.loop_dev:
            subprocess.run(
                ['udisksctl', 'unmount', '-b', cls.loop_dev,
                 '--no-user-interaction'],
                capture_output=True)
            subprocess.run(['sudo', 'losetup', '-d', cls.loop_dev],
                           capture_output=True)
        if cls._work_dir:
            shutil.rmtree(cls._work_dir, ignore_errors=True)

    def test_set_btime_on_existing_file(self):
        """Set btime via exFAT raw block and verify it changes to the exact target."""
        from btime import _fix_exfat_raw

        files = sorted(self.target.iterdir())
        self.assertGreater(len(files), 0)
        f = files[0]
        fpath = str(f)

        bt_before = read_btime(fpath)
        self.assertIsNotNone(bt_before, 'btime should be readable')

        target_dt = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        expected_ts = int(target_dt.timestamp())
        self.assertNotEqual(bt_before, expected_ts,
                            'btime should differ from target before fix')

        _fix_exfat_raw(fpath, target_dt, dry_run=False)

        bt_after = read_btime(fpath)
        self.assertIsNotNone(bt_after, 'btime should be readable after fix')
        self.assertEqual(
            bt_after, expected_ts,
            f'btime should be exactly {expected_ts} ({target_dt}), '
            f'got {bt_after}')

    def test_multiple_files_get_correct_btime(self):
        """Different files get different btimes via exFAT raw block."""
        from btime import _fix_exfat_raw

        files = sorted(self.target.iterdir())
        self.assertGreaterEqual(len(files), 3)

        targets = [
            (files[0], datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)),
            (files[1], datetime(2025, 6, 15, 12, 30, 0, tzinfo=timezone.utc)),
            (files[2], datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc)),
        ]

        for f, dt in targets:
            expected = int(dt.timestamp())
            _fix_exfat_raw(str(f), dt, dry_run=False)
            bt = read_btime(str(f))
            self.assertEqual(
                bt, expected,
                f'{f.name}: expected {expected} ({dt}), got {bt}')

    def test_exfat_raw_is_registered_as_method(self):
        """The exfat_raw method is recognized by resolve_method."""
        from btime import resolve_method, needs_processing_after, fix_file, detect_fs

        self.assertEqual(resolve_method('exfat_raw', 'exfat'), 'exfat_raw')
        self.assertTrue(needs_processing_after('exfat_raw'))


if __name__ == '__main__':
    unittest.main()
