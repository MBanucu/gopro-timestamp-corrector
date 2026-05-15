import unittest
import subprocess
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from shared import decompress_sparse_image

import btime


class TestBtimeFsDetection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mount_point = None

        gz_path = Path(__file__).parent / 'sdcard.img.gz'
        if not gz_path.exists():
            raise unittest.SkipTest(f"Compressed image not found at {gz_path}")
        img_path = Path(__file__).parent / 'sdcard.img'
        cls.img_path = decompress_sparse_image(gz_path, img_path)

        try:
            res = subprocess.run(
                ['udisksctl', 'loop-setup', '-f', str(cls.img_path), '--no-user-interaction'],
                capture_output=True, text=True)
            if res.returncode != 0:
                raise unittest.SkipTest("udisksctl loop-setup failed")
            m = re.search(r'as (/dev/loop\d+)', res.stdout)
            if not m:
                raise unittest.SkipTest("Could not parse loop device")
            cls.loop_dev = m.group(1)

            res = subprocess.run(
                ['udisksctl', 'mount', '-b', cls.loop_dev, '--no-user-interaction'],
                capture_output=True, text=True)
            if res.returncode != 0:
                if 'AlreadyMounted' in res.stderr:
                    m = re.search(r"at `([^`]+)'", res.stderr)
                    if m:
                        cls.mount_point = m.group(1)
                if not cls.mount_point:
                    raise unittest.SkipTest("udisksctl mount failed")
            else:
                m = re.search(r'at ([^ \n]+)', res.stdout)
                if m:
                    cls.mount_point = m.group(1).rstrip('.')
                else:
                    raise unittest.SkipTest("Could not parse mount point")
            cls.test_path = Path(cls.mount_point) / 'DCIM' / '100GOPRO'
        except FileNotFoundError:
            raise unittest.SkipTest("udisksctl not found")

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, 'loop_dev', None):
            subprocess.run(
                ['udisksctl', 'unmount', '-b', cls.loop_dev, '--no-user-interaction'],
                capture_output=True)
            subprocess.run(
                ['udisksctl', 'loop-delete', '-b', cls.loop_dev, '--no-user-interaction'],
                capture_output=True)

    def test_detect_fs_exfat(self):
        fs = btime.detect_fs(self.test_path)
        self.assertEqual(fs, 'exfat')

    def test_resolve_device_returns_something(self):
        dev = btime._resolve_device(self.test_path)
        self.assertIsNotNone(dev)
        self.assertTrue(dev.startswith('/dev/'))

    def test_setup_dry_run_fuse(self):
        if not shutil.which('mount.exfat-fuse'):
            self.skipTest("mount.exfat-fuse not available")
        if not shutil.which('faketime'):
            self.skipTest("faketime not available")
        ctx = btime.setup('fuse', self.test_path, timedelta(hours=-2), dry_run=True)
        self.assertIsNotNone(ctx)
        self.assertIn('device', ctx)
        self.assertIn('offset', ctx)


class TestBtimePureFunctions(unittest.TestCase):
    def test_resolve_method_auto_ext4(self):
        self.assertEqual(btime.resolve_method('auto', 'ext4'), 'debugfs')

    def test_resolve_method_auto_exfat(self):
        self.assertEqual(btime.resolve_method('auto', 'exfat'), 'fuse')

    def test_resolve_method_auto_vfat(self):
        self.assertEqual(btime.resolve_method('auto', 'vfat'), 'fuse')

    def test_resolve_method_explicit(self):
        self.assertEqual(btime.resolve_method('debugfs', 'exfat'), 'debugfs')
        self.assertEqual(btime.resolve_method('fuse', 'ext4'), 'fuse')
        self.assertEqual(btime.resolve_method('clock', 'ext4'), 'clock')

    def test_needs_processing_before(self):
        self.assertTrue(btime.needs_processing_before('fuse'))
        self.assertFalse(btime.needs_processing_before('debugfs'))
        self.assertFalse(btime.needs_processing_before('clock'))

    def test_needs_processing_after(self):
        self.assertTrue(btime.needs_processing_after('debugfs'))
        self.assertFalse(btime.needs_processing_after('fuse'))
        self.assertFalse(btime.needs_processing_after('clock'))

    def test_setup_dry_run_clock(self):
        ctx = btime.setup('clock', '/some/path', timedelta(), dry_run=True)
        self.assertIn('ntp_stopped', ctx)
        self.assertTrue(ctx['ntp_stopped'])

    def test_setup_debugfs_noop(self):
        ctx = btime.setup('debugfs', '/some/path', timedelta(), dry_run=True)
        self.assertEqual(ctx, {})

    def test_teardown_dry_run(self):
        for method in ('fuse', 'debugfs', 'clock'):
            with self.subTest(method=method):
                btime.teardown(method, {}, dry_run=True)

    def test_fix_file_dry_run_debugfs(self):
        import tempfile
        with tempfile.NamedTemporaryFile() as f:
            btime.fix_file('debugfs', f.name, datetime.now(timezone.utc), {}, dry_run=True)

    def test_fix_file_dry_run_clock(self):
        btime.fix_file('clock', '/nonexistent/file', datetime.now(timezone.utc), {}, dry_run=True)


if __name__ == '__main__':
    unittest.main()
