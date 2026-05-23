"""Quick performance test: ExifTool singleton daemon reuse.

Measures how fast 12 metadata writes are with the shared daemon.
"""
import time, unittest
from datetime import datetime, timezone
from pathlib import Path

_BD = str(Path(__file__).resolve().parent.parent.parent / 'src')
import sys
if _BD not in sys.path:
    sys.path.insert(0, _BD)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / 'test'))


class TestExifToolPerf(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from test.shared import decompress_sparse_image, prepare_sparse_image, \
            setup_loop_device, teardown_loop_device
        gz = Path(__file__).parent.parent / 'sdcard.img.gz'
        if not gz.exists():
            raise unittest.SkipTest('sdcard.img.gz not found')
        cached = Path(__file__).parent.parent / 'sdcard.img'
        decompress_sparse_image(gz, cached)
        cls._work, cls._img = prepare_sparse_image(gz, prefix='perf2_')
        try:
            cls._loop, cls._mnt = setup_loop_device(str(cls._img))
        except Exception:
            teardown_loop_device(getattr(cls, '_loop', None))
            import shutil; shutil.rmtree(cls._work, ignore_errors=True)
            raise
        cls._target = Path(cls._mnt) / 'DCIM' / '100GOPRO'
        if not cls._target.exists():
            teardown_loop_device(cls._loop, cls._mnt)
            shutil.rmtree(cls._work, ignore_errors=True)
            raise unittest.SkipTest('100GOPRO not found')
        cls._files = sorted(cls._target.glob('*'))

    @classmethod
    def tearDownClass(cls):
        from test.shared import teardown_loop_device
        teardown_loop_device(cls._loop, cls._mnt)
        import shutil; shutil.rmtree(cls._work, ignore_errors=True)

    def test_singleton_speed(self):
        """Singleton daemon: 12 metadata writes should complete in <10s total."""
        from exiftool_session import ExifToolSession
        # First run — starts the daemon
        t0 = time.perf_counter()
        for f in self._files:
            with ExifToolSession(connect=None) as s:
                s.write_embedded(f, datetime.now(timezone.utc))
        t1 = time.perf_counter()

        # Second run — reuses the daemon
        t2 = time.perf_counter()
        for f in self._files:
            with ExifToolSession(connect=None) as s:
                s.write_embedded(f, datetime.now(timezone.utc))
        t3 = time.perf_counter()

        first = t1 - t0
        second = t3 - t2
        print(f'  first 12 writes (daemon start): {first:.2f}s')
        print(f'  second 12 writes (reused):      {second:.2f}s')
        if second > 0:
            print(f'  speedup: {first/second:.1f}x')
        self.assertLess(second, 10, 'Singleton reused writes too slow (>10s)')
