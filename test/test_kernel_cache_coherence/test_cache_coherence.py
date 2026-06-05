"""Tests for project-level exFAT strategy classes.

These test the project's own ExfatRawStrategy and ExfatRawMtimeStrategy
wrappers, not the external exfat-raw library directly.
"""

import shutil
import unittest
from datetime import datetime, timezone
from pathlib import Path


class _CacheLayer(unittest.TestCase):
    """Base class: creates fresh loop device for strategy tests."""

    files: list[Path]
    target: Path

    @classmethod
    def setUpClass(cls):
        from test.shared import decompress_sparse_image, prepare_sparse_image, \
            setup_loop_device, teardown_loop_device
        from exfat_raw import ExfatRawIO
        cls._io = ExfatRawIO()
        gz = Path(__file__).parent.parent / 'sdcard.img.gz'
        if not gz.exists():
            raise unittest.SkipTest('sdcard.img.gz not found')
        cls._work, cls._img = prepare_sparse_image(gz, prefix='cache_coh_')
        cls._loop, cls._mnt = setup_loop_device(str(cls._img))
        cls.addClassCleanup(teardown_loop_device, cls._loop, cls._mnt)
        cls.addClassCleanup(shutil.rmtree, cls._work, ignore_errors=True)
        cls.target = Path(cls._mnt) / 'DCIM' / '100GOPRO'
        if not cls.target.exists():
            raise unittest.SkipTest('100GOPRO not found')
        cls.files = sorted(cls.target.glob('*'))


class TestExfatRawStrategy(_CacheLayer):
    """Layer 2a: ExfatRawStrategy.fix_file updates both raw + cache."""

    def test_fix_file_updates_mtime(self):
        from exfat_raw import ExfatRawFilesystem, ExfatRawOps
        from strategies.exfat_raw import ExfatRawStrategy
        ops = ExfatRawOps(self._io, ExfatRawFilesystem(self._io))
        strategy = ExfatRawStrategy(ops)
        ts = 1778770800.0
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        strategy.fix_file(str(self.files[0]), dt, {}, dry_run=False)
        raw = ops.read_mtime_raw(str(self.files[0]))
        self.assertIsNotNone(raw)
        self.assertEqual(raw, int(ts))


class TestExfatRawMtimeStrategy(_CacheLayer):
    """Layer 2b: ExfatRawMtimeStrategy.write_mtime updates both raw + cache."""

    def test_write_mtime_updates_mtime(self):
        from exfat_raw import ExfatRawFilesystem, ExfatRawOps
        from strategies.mtime import ExfatRawMtimeStrategy
        ops = ExfatRawOps(self._io, ExfatRawFilesystem(self._io))
        strategy = ExfatRawMtimeStrategy(ops)
        ts = 1778770900.0
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        strategy.write_mtime(str(self.files[0]), dt)
        raw = ops.read_mtime_raw(str(self.files[0]))
        self.assertIsNotNone(raw)
        self.assertEqual(raw, int(ts))
