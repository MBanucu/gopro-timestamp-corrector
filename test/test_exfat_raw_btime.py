"""Integration test for exFAT raw block btime strategy."""
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


def _ops():
    from strategies.exfat_raw import ExfatRawIO, ExfatRawFilesystem, ExfatRawOps
    io = ExfatRawIO()
    return ExfatRawOps(io, ExfatRawFilesystem(io))


class TestExfatRawBtime(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        gz_path = Path(__file__).parent / 'sdcard.img.gz'
        if not gz_path.exists():
            raise unittest.SkipTest('sdcard.img.gz not found')

        from test.shared import decompress_sparse_image, setup_loop_device, teardown_loop_device
        cls._teardown = teardown_loop_device

        cached = Path(__file__).parent / 'sdcard.img'
        decompress_sparse_image(gz_path, cached)

        cls._work_dir = Path(tempfile.mkdtemp(prefix='gopro_raw_test_'))
        cls.img_path = cls._work_dir / 'sdcard.img'
        subprocess.run(['cp', '--sparse=always', str(cached), str(cls.img_path)],
                       check=True, capture_output=True)

        cls.loop_dev, cls.mount_point = setup_loop_device(cls.img_path)
        cls.target = Path(cls.mount_point) / 'DCIM' / '100GOPRO'
        if not cls.target.exists():
            cls._teardown(cls.loop_dev, cls.mount_point)
            raise unittest.SkipTest(f'{cls.target} not found')

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, '_teardown') and cls.loop_dev:
            cls._teardown(cls.loop_dev, cls.mount_point)
        if cls._work_dir:
            shutil.rmtree(cls._work_dir, ignore_errors=True)

    def test_set_btime_on_existing_file(self):
        ops = _ops()
        files = sorted(self.target.iterdir())
        self.assertGreater(len(files), 0)
        f = files[0]
        fpath = str(f)

        target_dt = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        expected_ts = int(target_dt.timestamp())

        raw_before = ops.read_btime_raw(fpath)
        self.assertIsNotNone(raw_before, 'raw btime should be readable')
        self.assertNotEqual(raw_before, expected_ts,
                            'btime should differ from target before fix')

        ops.fix_exfat_raw(fpath, target_dt, dry_run=False)

        raw_after = ops.read_btime_raw(fpath)
        self.assertEqual(
            raw_after, expected_ts,
            f'raw btime should be exactly {expected_ts} ({target_dt}), '
            f'got {raw_after}')

    def test_multiple_files_get_correct_btime(self):
        ops = _ops()
        files = sorted(self.target.iterdir())
        self.assertGreaterEqual(len(files), 3)

        targets = [
            (files[0], datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)),
            (files[1], datetime(2025, 6, 15, 12, 30, 0, tzinfo=timezone.utc)),
            (files[2], datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc)),
        ]

        for f, dt in targets:
            expected = int(dt.timestamp())
            ops.fix_exfat_raw(str(f), dt, dry_run=False)
            bt_raw = ops.read_btime_raw(str(f))
            self.assertEqual(
                bt_raw, expected,
                f'{f.name}: expected {expected} ({dt}), got {bt_raw}')

    def test_exfat_raw_is_registered_as_method(self):
        from btime import resolve_method, needs_processing_after, fix_file, detect_fs
        self.assertEqual(resolve_method('exfat_raw', 'exfat'), 'exfat_raw')
        self.assertTrue(needs_processing_after('exfat_raw'))

    def test_read_exfat_btime_raw_after_write(self):
        ops = _ops()
        files = sorted(self.target.iterdir())
        self.assertGreater(len(files), 0)
        f = str(files[0])

        target_dt = datetime(2025, 12, 25, 10, 30, 45, 120000, tzinfo=timezone.utc)
        expected_ts = int(target_dt.timestamp())

        ops.fix_exfat_raw(f, target_dt, dry_run=False)

        raw_bt = ops.read_btime_raw(f)
        self.assertIsNotNone(raw_bt, 'read_btime_raw should return a value')
        self.assertEqual(
            raw_bt, expected_ts,
            f'read_btime_raw should return {expected_ts} ({target_dt}), '
            f'got {raw_bt}')

    def test_read_exfat_btime_raw_returns_none_on_bad_path(self):
        from strategies.exfat_raw import exfat_ops
        result = exfat_ops.read_btime_raw('/nonexistent/file.mp4')
        self.assertIsNone(result)

    def test_exfat_raw_read_strategy_read_btime_raw(self):
        from strategies.exfat_raw import ExfatRawIO, ExfatRawFilesystem, \
            ExfatRawOps, ExfatRawReadStrategy, exfat_ops

        files = sorted(self.target.iterdir())
        self.assertGreater(len(files), 0)
        f = str(files[0])

        io = ExfatRawIO()
        fs = ExfatRawFilesystem(io)
        ops = ExfatRawOps(io, fs)
        strategy = ExfatRawReadStrategy(ops)
        strategy_val = strategy.read_btime_raw(f)
        direct_val = exfat_ops.read_btime_raw(f)

        self.assertEqual(
            strategy_val, direct_val,
            'ExfatRawReadStrategy.read_btime_raw should match read_btime_raw')


if __name__ == '__main__':
    unittest.main()
