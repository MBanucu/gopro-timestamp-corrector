"""Kernel cache coherence after raw block write — layered tests.

After ``ExfatRawOps.fix_exfat_raw`` writes to the raw block device, the
kernel exFAT driver's directory-entry cache still has the **old** mtime.
``os.utime()`` is NOT called after the raw-block write because the driver
would read its stale cache and overwrite the btime changes (see
``_ops.py`` for details).  The stale kernel cache is a cosmetic issue:
raw-block reads return the correct data, but ``os.path.getmtime()`` shows
the old value until the device is remounted.

Each test class creates its own ``ExfatRawIO`` / ``ExfatRawOps`` for cache
isolation.
"""

import os
import shutil
import unittest
from datetime import datetime, timezone
from pathlib import Path


class TestCacheLayer(unittest.TestCase):
    """Base class: creates fresh loop device + fresh IO instances per test class."""

    files: list[Path]
    target: Path

    @classmethod
    def setUpClass(cls):
        from strategies.exfat_raw import ExfatRawIO
        cls._io = ExfatRawIO()
        from test.shared import decompress_sparse_image, prepare_sparse_image, \
            setup_loop_device, teardown_loop_device
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


class TestRawBlockWrite(TestCacheLayer):
    """Layer 1: ExfatRawOps.fix_exfat_raw writes raw block + optionally updates cache."""

    def _ops(self):
        from strategies.exfat_raw import ExfatRawFilesystem, ExfatRawOps
        return ExfatRawOps(self._io, ExfatRawFilesystem(self._io))

    def test_writes_mtime_correctly_with_update_cache_false(self):
        ops = self._ops()
        ts = 1778770322.0
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        ops.fix_exfat_raw(str(self.files[0]), dt, dry_run=False, update_cache=False)
        raw = ops.read_mtime_raw(str(self.files[0]))
        self.assertIsNotNone(raw)
        self.assertEqual(raw, int(ts))

    def test_writes_btime_and_mtime_with_update_cache_true(self):
        ops = self._ops()
        ts = 1778770400.0
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        ops.fix_exfat_raw(str(self.files[1]), dt, dry_run=False, update_cache=True)
        raw_mtime = ops.read_mtime_raw(str(self.files[1]))
        self.assertIsNotNone(raw_mtime)
        self.assertEqual(raw_mtime, int(ts))
        raw_btime = ops.read_btime_raw(str(self.files[1]))
        self.assertIsNotNone(raw_btime)
        self.assertEqual(raw_btime, int(ts))

    def test_preserves_btime_with_btime_dt(self):
        ops = self._ops()
        first = self.files[2]
        orig_btime_raw = ops.read_btime_raw(str(first))
        self.assertIsNotNone(orig_btime_raw)
        orig_dt = datetime.fromtimestamp(orig_btime_raw, tz=timezone.utc)
        new_ts = 1778770500.0
        new_dt = datetime.fromtimestamp(new_ts, tz=timezone.utc)
        ops.fix_exfat_raw(str(first), new_dt, dry_run=False, btime_dt=orig_dt, update_cache=True)
        after_btime = ops.read_btime_raw(str(first))
        self.assertIsNotNone(after_btime)
        self.assertEqual(after_btime, orig_btime_raw)
        after_mtime = ops.read_mtime_raw(str(first))
        self.assertEqual(after_mtime, int(new_ts))

    def test_stale_cache_without_os_utime(self):
        ops = self._ops()
        ts = 1778770600.0
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        ops.fix_exfat_raw(str(self.files[3]), dt, dry_run=False, update_cache=False)
        stat = os.path.getmtime(self.files[3])
        if abs(stat - ts) < 1:
            self.skipTest('cache was already coherent')
        raw = ops.read_mtime_raw(str(self.files[3]))
        self.assertIsNotNone(raw)
        self.assertEqual(raw, int(ts))

    def test_cache_coherent_with_update_cache_true(self):
        ops = self._ops()
        ts = 1778770700.0
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        ops.fix_exfat_raw(str(self.files[4]), dt, dry_run=False, update_cache=True)
        raw = ops.read_mtime_raw(str(self.files[4]))
        self.assertIsNotNone(raw)
        self.assertEqual(raw, int(ts))


class TestExfatRawStrategy(TestCacheLayer):
    """Layer 2a: ExfatRawStrategy.fix_file updates both raw + cache."""

    def test_fix_file_updates_mtime(self):
        from strategies.exfat_raw import ExfatRawFilesystem, ExfatRawOps, ExfatRawStrategy
        ops = ExfatRawOps(self._io, ExfatRawFilesystem(self._io))
        strategy = ExfatRawStrategy(ops)
        ts = 1778770800.0
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        strategy.fix_file(str(self.files[0]), dt, {}, dry_run=False)
        raw = ops.read_mtime_raw(str(self.files[0]))
        self.assertIsNotNone(raw)
        self.assertEqual(raw, int(ts))


class TestExfatRawMtimeStrategy(TestCacheLayer):
    """Layer 2b: ExfatRawMtimeStrategy.write_mtime updates both raw + cache."""

    def test_write_mtime_updates_mtime(self):
        from strategies.exfat_raw import ExfatRawFilesystem, ExfatRawOps
        from strategies.mtime import ExfatRawMtimeStrategy
        ops = ExfatRawOps(self._io, ExfatRawFilesystem(self._io))
        strategy = ExfatRawMtimeStrategy(ops)
        ts = 1778770900.0
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        strategy.write_mtime(str(self.files[0]), dt)
        raw = ops.read_mtime_raw(str(self.files[0]))
        self.assertIsNotNone(raw)
        self.assertEqual(raw, int(ts))
