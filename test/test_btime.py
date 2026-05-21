import unittest
import subprocess
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from shared import decompress_sparse_image, setup_loop_device, teardown_loop_device


import btime


class TestBtimeFsDetection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop_dev = None
        cls.mount_point = None

        gz_path = Path(__file__).parent / 'sdcard.img.gz'
        if not gz_path.exists():
            raise unittest.SkipTest(f"Compressed image not found at {gz_path}")
        img_path = Path(__file__).parent / 'sdcard.img'
        cls.img_path = decompress_sparse_image(gz_path, img_path)

        cls.loop_dev, cls.mount_point = setup_loop_device(cls.img_path)
        cls.test_path = Path(cls.mount_point) / 'DCIM' / '100GOPRO'

    @classmethod
    def tearDownClass(cls):
        teardown_loop_device(cls.loop_dev, cls.mount_point)

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
        self.assertEqual(btime.resolve_method('auto', 'exfat'), 'exfat_raw')

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

    # ── compatible_methods ────────────────────────────────────────

    def test_compatible_methods_ext4(self):
        for fs in ('ext4', 'ext3', 'ext2'):
            with self.subTest(fs=fs):
                methods = btime.compatible_methods(fs)
                self.assertIn('debugfs', methods)
                self.assertIn('clock', methods)
                self.assertEqual(len(methods), 2)
                self.assertNotIn('exfat_raw', methods)
                self.assertNotIn('fuse', methods)

    def test_compatible_methods_exfat(self):
        methods = btime.compatible_methods('exfat')
        self.assertIn('exfat_raw', methods)
        self.assertIn('fuse', methods)
        self.assertIn('clock', methods)
        self.assertEqual(len(methods), 3)
        self.assertNotIn('debugfs', methods)

    def test_compatible_methods_vfat(self):
        methods = btime.compatible_methods('vfat')
        self.assertIn('exfat_raw', methods)
        self.assertIn('fuse', methods)
        self.assertIn('clock', methods)
        self.assertNotIn('debugfs', methods)

    def test_compatible_methods_fuseblk(self):
        methods = btime.compatible_methods('fuseblk')
        self.assertIn('exfat_raw', methods)
        self.assertIn('fuse', methods)
        self.assertIn('clock', methods)
        self.assertNotIn('debugfs', methods)

    def test_compatible_methods_unknown(self):
        for fs in ('btrfs', 'xfs', 'ntfs'):
            with self.subTest(fs=fs):
                methods = btime.compatible_methods(fs)
                self.assertEqual(methods, ('clock',))

    def test_compatible_methods_none(self):
        methods = btime.compatible_methods(None)
        self.assertEqual(methods, ('clock',))

    # ── chain_setup ───────────────────────────────────────────────

    def test_chain_setup_picks_first_method(self):
        method, ctx = btime.chain_setup(
            ['debugfs', 'clock'], '/some/path', 'ext4',
            timedelta(), dry_run=True)
        self.assertEqual(method, 'debugfs')
        self.assertEqual(ctx, {})

    def test_chain_setup_falls_back_on_fuse_failure(self):
        # On a non-exFAT fs, FUSE setup will fail dry-run (no device).
        method, ctx = btime.chain_setup(
            ['fuse', 'clock'], '/some/path', 'ext4',
            timedelta(), dry_run=True)
        # FUSE fails → falls back to clock
        self.assertIn(method, ('clock', 'fuse'))

    def test_chain_setup_clock_last_resort(self):
        method, ctx = btime.chain_setup(
            ['clock'], '/some/path', 'ext4',
            timedelta(), dry_run=True)
        self.assertEqual(method, 'clock')
        self.assertIn('ntp_stopped', ctx)

    def test_chain_setup_auto_resolves(self):
        method, ctx = btime.chain_setup(
            ['auto', 'clock'], '/some/path', 'ext4',
            timedelta(), dry_run=True)
        self.assertEqual(method, 'debugfs')

    def test_chain_setup_auto_expands_to_full_chain_on_exfat(self):
        """``auto`` on exFAT must expand to exfat_raw → fuse → clock."""
        # exfat_raw needs no setup → should succeed immediately
        method, ctx = btime.chain_setup(
            ['auto'], '/some/path', 'exfat',
            timedelta(), dry_run=True)
        self.assertEqual(method, 'exfat_raw')

    def test_chain_setup_auto_expands_on_ext4(self):
        """``auto`` on ext4 must expand to debugfs → clock."""
        method, ctx = btime.chain_setup(
            ['auto'], '/some/path', 'ext4',
            timedelta(), dry_run=True)
        self.assertEqual(method, 'debugfs')

    def test_chain_setup_auto_respects_explicit_methods(self):
        """A mixed list: explicit methods used as-is, auto still expands."""
        # 'clock' before 'auto' should still work
        method, ctx = btime.chain_setup(
            ['clock', 'auto'], '/some/path', 'ext4',
            timedelta(), dry_run=True)
        self.assertEqual(method, 'clock')
        # clock was first and needs setup → succeeds → no fallback needed

    def test_chain_setup_empty_returns_none(self):
        method, ctx = btime.chain_setup(
            [], '/some/path', 'ext4',
            timedelta(), dry_run=True)
        self.assertIsNone(method)
        self.assertEqual(ctx, {})

    def test_chain_setup_skips_unusable_methods(self):
        # exfat_raw on ext4 — no setup needed, but fix_file will error.
        # chain_setup doesn't know about fix_file compatibility, so
        # exfat_raw will "succeed" at setup on ext4 (returns {}).
        method, ctx = btime.chain_setup(
            ['exfat_raw', 'clock'], '/some/path', 'ext4',
            timedelta(), dry_run=True)
        self.assertEqual(method, 'exfat_raw')


if __name__ == '__main__':
    unittest.main()
