"""Unit tests for core modules: parsing, strategies, probe utilities."""

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


class TestParseDt(unittest.TestCase):
    """_parse_dt must correctly convert timezone offsets to UTC."""

    def _parse(self, val: str):
        from exiftool_session import _parse_dt
        return _parse_dt(val)

    def test_utc_plus_09(self):
        """+09:00 offset: local 14:52 → UTC 05:52."""
        dt = self._parse('2026:05:14 14:52:00+09:00')
        self.assertEqual(dt, datetime(2026, 5, 14, 5, 52, 0, tzinfo=timezone.utc))

    def test_utc_minus_05(self):
        """-05:00 offset: local 14:52 → UTC 19:52."""
        dt = self._parse('2026:05:14 14:52:00-05:00')
        self.assertEqual(dt, datetime(2026, 5, 14, 19, 52, 0, tzinfo=timezone.utc))

    def test_utc_zero_offset(self):
        """+00:00 offset: unchanged."""
        dt = self._parse('2026:05:14 14:52:00+00:00')
        self.assertEqual(dt, datetime(2026, 5, 14, 14, 52, 0, tzinfo=timezone.utc))

    def test_no_offset(self):
        """No offset: treated as UTC (exiftool QuickTimeUTC=1 output)."""
        dt = self._parse('2026:05:14 14:52:00')
        self.assertEqual(dt, datetime(2026, 5, 14, 14, 52, 0, tzinfo=timezone.utc))

    def test_z_suffix(self):
        """Z suffix: UTC."""
        dt = self._parse('2026:05:14 14:52:00Z')
        self.assertEqual(dt, datetime(2026, 5, 14, 14, 52, 0, tzinfo=timezone.utc))

    def test_fractional_seconds(self):
        """Fractional seconds with offset."""
        dt = self._parse('2026:05:14 14:52:00.123+09:00')
        self.assertEqual(dt.microsecond, 123000)
        self.assertEqual(dt.hour, 5)
        self.assertEqual(dt.minute, 52)

    def test_empty(self):
        """Empty string returns None."""
        self.assertIsNone(self._parse(''))
        self.assertIsNone(self._parse('  '))

    def test_invalid(self):
        """Invalid format returns None."""
        self.assertIsNone(self._parse('not-a-date'))
        self.assertIsNone(self._parse('2026/05/14 14:52:00'))


class TestMountStrategyDetection(unittest.TestCase):
    """detect_strategy must correctly identify source types."""

    def test_directory_is_already_mounted(self):
        from strategies.mount import detect_strategy, AlreadyMountedStrategy
        strategy = detect_strategy('.')
        self.assertIsInstance(strategy, AlreadyMountedStrategy)

    def test_img_file_is_image(self):
        from strategies.mount import detect_strategy, ImageMountStrategy
        tf = tempfile.NamedTemporaryFile(suffix='.img', delete=False)
        tf.close()
        try:
            strategy = detect_strategy(tf.name)
            self.assertIsInstance(strategy, ImageMountStrategy)
        finally:
            os.unlink(tf.name)

    def test_unknown_source_raises(self):
        from strategies.mount import detect_strategy, MountError
        with self.assertRaises(MountError):
            detect_strategy('/nonexistent/path')

    def test_registry_keys(self):
        from strategies.mount import REGISTRY
        self.assertIn('already_mounted', REGISTRY)
        self.assertIn('image', REGISTRY)


class TestMtimeStrategySelection(unittest.TestCase):
    """Mtime strategy selection must match filesystem and btime method."""

    def test_skip_when_btime_handles_mtime(self):
        """SkipMtimeStrategy when btime method is exfat_raw."""
        from writer import Writer
        from strategies.mtime import SkipMtimeStrategy
        writer = Writer.__new__(Writer)
        writer._b_method = 'exfat_raw'
        strategy = writer._resolve_mtime_strategy()
        self.assertIsInstance(strategy, SkipMtimeStrategy)

    def test_os_utime_on_ext4(self):
        """OsUtimeMtimeStrategy on ext4 (where os.utime() works)."""
        from writer import Writer
        from strategies.mtime import OsUtimeMtimeStrategy
        import btime
        writer = Writer.__new__(Writer)
        writer._b_method = 'clock'
        writer.target_dir = Path('.')
        strategy = writer._resolve_mtime_strategy()
        # '.' is ext4 (or whatever the root fs is), not exfat
        fs = btime.detect_fs('.')
        if fs not in ('exfat', 'fuse', 'exfat_raw'):
            self.assertIsInstance(strategy, OsUtimeMtimeStrategy)

    def test_btime_handles_mtime_method(self):
        """_btime_handles_mtime returns True only for exfat_raw."""
        from writer import Writer
        w = Writer.__new__(Writer)
        w._b_method = 'exfat_raw'
        self.assertTrue(w._btime_handles_mtime())
        w._b_method = 'fuse'
        self.assertFalse(w._btime_handles_mtime())
        w._b_method = 'clock'
        self.assertFalse(w._btime_handles_mtime())


class TestNormalizeBtime(unittest.TestCase):
    """_normalize_btime must handle various input types."""

    def _norm(self, val):
        from writer import _normalize_btime
        return _normalize_btime(val)

    def test_off_returns_empty(self):
        self.assertEqual(self._norm('off'), [])
        self.assertEqual(self._norm(None), [])
        self.assertEqual(self._norm(False), [])

    def test_single_string(self):
        self.assertEqual(self._norm('exfat_raw'), ['exfat_raw'])

    def test_list(self):
        self.assertEqual(self._norm(['exfat_raw', 'fuse']), ['exfat_raw', 'fuse'])

    def test_tuple(self):
        self.assertEqual(self._norm(('exfat_raw',)), ['exfat_raw'])


class TestEnvCheckProbes(unittest.TestCase):
    """env_check capabilities must report correctly."""

    def test_tool_availability(self):
        from env_check import _tool
        dd = _tool('dd')
        self.assertTrue(dd.available)
        self.assertIsNotNone(dd.path)
        nonexistent = _tool('nonexistent_tool_xyz')
        self.assertFalse(nonexistent.available)

    def test_sudo_check(self):
        from env_check import _check_sudo
        # On CI and local with passwordless sudo, this should be True
        try:
            import subprocess
            r = subprocess.run(['sudo', '-n', 'true'], capture_output=True)
            expected = r.returncode == 0
        except Exception:
            expected = False
        self.assertEqual(_check_sudo(), expected)

    def test_check_env_basic(self):
        from env_check import check_env
        report = check_env()
        self.assertIsNotNone(report)
        self.assertEqual(report.platform, sys.platform)
        self.assertIsNotNone(report.exiftool)

    def test_probe_functions_exist(self):
        from probe import (
            probe_stat_btime, probe_statx_btime, probe_utime,
            probe_btime, probe_exfat_btime,
        )
        # Just verify they're importable and callable
        self.assertTrue(callable(probe_stat_btime))
        self.assertTrue(callable(probe_statx_btime))
        self.assertTrue(callable(probe_utime))
        self.assertTrue(callable(probe_btime))
        self.assertTrue(callable(probe_exfat_btime))
