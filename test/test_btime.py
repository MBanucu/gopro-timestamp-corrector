import unittest
import subprocess
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from shared import prepare_sparse_image, setup_loop_device, teardown_loop_device


import btime


class TestBtimeFsDetection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop_dev = None
        cls.mount_point = None
        cls._work_dir = None

        gz_path = Path(__file__).parent / 'sdcard.img.gz'
        if not gz_path.exists():
            raise unittest.SkipTest(f"Compressed image not found at {gz_path}")
        cls._work_dir, cls.img_path = prepare_sparse_image(gz_path)

        cls.loop_dev, cls.mount_point = setup_loop_device(cls.img_path)
        cls.addClassCleanup(teardown_loop_device, cls.loop_dev, cls.mount_point)
        cls.addClassCleanup(shutil.rmtree, cls._work_dir, ignore_errors=True)
        cls.test_path = Path(cls.mount_point) / 'DCIM' / '100GOPRO'

    def test_detect_fs_exfat(self):
        fs = btime.detect_fs(self.test_path)
        self.assertEqual(fs, 'exfat')

    def test_resolve_device_returns_something(self):
        dev = btime._resolve_device(self.test_path)
        self.assertIsNotNone(dev)
        self.assertTrue(dev.startswith('/dev/'))

class TestBtimePureFunctions(unittest.TestCase):
    def test_resolve_method_auto_ext4(self):
        self.assertEqual(btime.resolve_method('auto', 'ext4'), 'debugfs')

    def test_resolve_method_auto_exfat(self):
        self.assertEqual(btime.resolve_method('auto', 'exfat'), 'exfat_raw')

    def test_resolve_method_auto_vfat(self):
        self.assertIsNone(btime.resolve_method('auto', 'vfat'))

    def test_resolve_method_explicit(self):
        self.assertEqual(btime.resolve_method('debugfs', 'exfat'), 'debugfs')
        self.assertIsNone(btime.resolve_method('fuse', 'ext4'))
        self.assertIsNone(btime.resolve_method('nonexistent', 'unknown'))

    def test_needs_processing_before(self):
        self.assertFalse(btime.needs_processing_before('fuse'))
        self.assertFalse(btime.needs_processing_before('debugfs'))

    def test_needs_processing_after(self):
        self.assertTrue(btime.needs_processing_after('debugfs'))
        self.assertFalse(btime.needs_processing_after('fuse'))

    def test_setup_debugfs_noop(self):
        ctx = btime.setup('debugfs', '/some/path', timedelta(), dry_run=True)
        self.assertEqual(ctx, {})

    def test_teardown_dry_run(self):
        btime.teardown('debugfs', {}, dry_run=True)

    def test_fix_file_dry_run_debugfs(self):
        import tempfile
        with tempfile.NamedTemporaryFile() as f:
            btime.fix_file('debugfs', f.name, datetime.now(timezone.utc), {}, dry_run=True)

    # ── compatible_methods ────────────────────────────────────────

    def test_compatible_methods_ext4(self):
        for fs in ('ext4', 'ext3', 'ext2'):
            with self.subTest(fs=fs):
                methods = btime.compatible_methods(fs)
                self.assertIn('debugfs', methods)
                self.assertEqual(len(methods), 1)
                self.assertNotIn('exfat_raw', methods)

    def test_compatible_methods_exfat(self):
        methods = btime.compatible_methods('exfat')
        self.assertIn('exfat_raw', methods)
        self.assertEqual(len(methods), 1)
        self.assertNotIn('debugfs', methods)
        self.assertNotIn('exfat_raw_read', methods)

    def test_compatible_methods_vfat(self):
        methods = btime.compatible_methods('vfat')
        self.assertIn('exfat_raw', methods)
        self.assertEqual(len(methods), 1)
        self.assertNotIn('debugfs', methods)
        self.assertNotIn('exfat_raw_read', methods)

    def test_compatible_methods_fuseblk(self):
        methods = btime.compatible_methods('fuseblk')
        self.assertIn('exfat_raw', methods)
        self.assertEqual(len(methods), 1)
        self.assertNotIn('debugfs', methods)
        self.assertNotIn('exfat_raw_read', methods)

    def test_compatible_methods_unknown(self):
        for fs in ('btrfs', 'xfs', 'ntfs'):
            with self.subTest(fs=fs):
                methods = btime.compatible_methods(fs)
                self.assertEqual(methods, ())

    def test_compatible_methods_none(self):
        methods = btime.compatible_methods(None)
        self.assertEqual(methods, ())

    # ── chain_setup ───────────────────────────────────────────────

    def test_chain_setup_picks_first_method(self):
        method, ctx = btime.chain_setup(
            ['debugfs'], '/some/path', 'ext4',
            timedelta(), dry_run=True)
        self.assertEqual(method, 'debugfs')
        self.assertEqual(ctx, {})

    def test_chain_setup_falls_back_on_fuse_failure(self):
        # On a non-exFAT fs, FUSE setup will fail dry-run (no device).
        method, ctx = btime.chain_setup(
            ['fuse'], '/some/path', 'ext4',
            timedelta(), dry_run=True)
        self.assertIsNone(method)

    def test_chain_setup_auto_resolves(self):
        method, ctx = btime.chain_setup(
            ['auto'], '/some/path', 'ext4',
            timedelta(), dry_run=True)
        self.assertEqual(method, 'debugfs')

    def test_chain_setup_auto_expands_to_full_chain_on_exfat(self):
        """``auto`` on exFAT must expand to exfat_raw."""
        method, ctx = btime.chain_setup(
            ['auto'], '/some/path', 'exfat',
            timedelta(), dry_run=True)
        self.assertEqual(method, 'exfat_raw')

    def test_chain_setup_auto_expands_on_ext4(self):
        """``auto`` on ext4 must expand to debugfs."""
        method, ctx = btime.chain_setup(
            ['auto'], '/some/path', 'ext4',
            timedelta(), dry_run=True)
        self.assertEqual(method, 'debugfs')

    def test_chain_setup_auto_respects_explicit_methods(self):
        """explicit methods used as-is, auto still expands."""
        method, ctx = btime.chain_setup(
            ['debugfs', 'auto'], '/some/path', 'ext4',
            timedelta(), dry_run=True)
        self.assertEqual(method, 'debugfs')

    def test_chain_setup_empty_returns_none(self):
        method, ctx = btime.chain_setup(
            [], '/some/path', 'ext4',
            timedelta(), dry_run=True)
        self.assertIsNone(method)
        self.assertEqual(ctx, {})

    def test_chain_setup_skips_unusable_methods(self):
        # exfat_raw is not viable on ext4 → no fallback available.
        method, ctx = btime.chain_setup(
            ['exfat_raw'], '/some/path', 'ext4',
            timedelta(), dry_run=True)
        self.assertIsNone(method)


if __name__ == '__main__':
    unittest.main()
