"""Quick batch-lock performance test."""
import time, unittest
from datetime import datetime, timezone
from pathlib import Path

_BD = str(Path(__file__).resolve().parent.parent.parent / 'src')
import sys
if _BD not in sys.path: sys.path.insert(0, _BD)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / 'test'))


class TestBatchPerf(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from test.shared import decompress_sparse_image, prepare_sparse_image, \
            setup_loop_device, teardown_loop_device
        gz = Path(__file__).parent.parent / 'sdcard.img.gz'
        if not gz.exists(): raise unittest.SkipTest('sdcard.img.gz not found')
        cached = Path(__file__).parent.parent / 'sdcard.img'
        decompress_sparse_image(gz, cached)
        cls._work, cls._img = prepare_sparse_image(gz, prefix='perf_')
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

    def test_batch_speed(self):
        """Batch metadata write with file lock should complete in <10s."""
        from exiftool_session import ExifToolSession
        pairs = [(f, datetime.now(timezone.utc)) for f in self._files]
        t0 = time.perf_counter()
        with ExifToolSession(connect=None) as s:
            ok = s.write_embedded_batch(pairs)
        elapsed = time.perf_counter() - t0
        print(f'  {len(pairs)} files in batch: {elapsed:.2f}s')
        self.assertTrue(ok, 'Batch write failed')
        self.assertLess(elapsed, 10, 'Batch write too slow (>10s)')
