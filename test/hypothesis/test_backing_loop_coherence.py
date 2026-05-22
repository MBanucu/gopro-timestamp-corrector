"""Hypothesis: loop device and backing file have separate page caches.

After ``os.pwrite`` on the backing file, a subsequent read through
the loop device (``sudo dd if=/dev/loopN``) may return stale data
because the loop device maintains its own bdev page cache.

Test: write via backing file, read back via loop device.
If values differ, the caches are separate.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

_BD = str(Path(__file__).resolve().parent.parent.parent / 'src')
if _BD not in os.environ.get('PYTHONPATH', ''):
    os.environ.setdefault('PYTHONPATH', _BD)


def _needs_image(test_case):
    gz = Path(__file__).parent.parent / 'sdcard.img.gz'
    if not gz.exists():
        test_case.skipTest('sdcard.img.gz not found')
    return gz


class TestBackingLoopCoherence(unittest.TestCase):
    """Verify backing file I/O and loop device I/O are coherent."""

    @classmethod
    def setUpClass(cls):
        from test.shared import decompress_sparse_image, prepare_sparse_image, \
            setup_loop_device, teardown_loop_device
        gz = _needs_image(cls)
        cached = Path(__file__).parent.parent / 'sdcard.img'
        decompress_sparse_image(gz, cached)
        cls._work, cls._img = prepare_sparse_image(gz, prefix='coherence_')
        try:
            cls._loop, cls._mnt = setup_loop_device(str(cls._img))
        except Exception:
            teardown_loop_device(getattr(cls, '_loop', None))
            import shutil
            shutil.rmtree(cls._work, ignore_errors=True)
            raise
        cls._dev_name = cls._loop.lstrip('/dev/')

    @classmethod
    def tearDownClass(cls):
        from test.shared import teardown_loop_device
        teardown_loop_device(cls._loop, cls._mnt)
        import shutil
        shutil.rmtree(cls._work, ignore_errors=True)

    def _backing_file(self):
        r = subprocess.run(
            ['cat', f'/sys/block/{self._dev_name}/loop/backing_file'],
            capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None

    def _read_via_backing(self, offset, size):
        back = self._backing_file()
        if not back or not os.access(back, os.R_OK):
            self.skipTest('backing file not accessible')
        fd = os.open(back, os.O_RDONLY)
        try:
            return os.pread(fd, size, offset)
        finally:
            os.close(fd)

    def _read_via_loop(self, offset, size):
        r = subprocess.run(
            ['sudo', 'dd', f'if={self._loop}', 'bs=1',
             f'skip={offset}', f'count={size}', 'status=none'],
            capture_output=True)
        return r.stdout

    def _write_via_backing(self, offset, data):
        back = self._backing_file()
        if not back or not os.access(back, os.W_OK):
            self.skipTest('backing file not accessible')
        fd = os.open(back, os.O_WRONLY)
        try:
            n = os.pwrite(fd, data, offset)
            assert n == len(data)
            os.fsync(fd)
        finally:
            os.close(fd)

    def _write_via_loop(self, offset, data):
        with tempfile.NamedTemporaryFile() as tf:
            tf.write(data)
            tf.flush()
            subprocess.run(
                ['sudo', 'dd', f'if={tf.name}', f'of={self._loop}',
                 'bs=1', f'seek={offset}', f'count={len(data)}',
                 'status=none', 'conv=fsync'],
                check=True, capture_output=True)

    # ── tests ────────────────────────────────────────────────

    def test_write_backing_read_backing(self):
        """Write via backing file, read via backing file (both os.pread/pwrite)."""
        self._write_via_backing(1000, b'BACKING_WRITE')
        val = self._read_via_backing(1000, 13)
        self.assertEqual(val, b'BACKING_WRITE')

    def test_write_loop_read_loop(self):
        """Write via loop device, read via loop device (both sudo dd)."""
        self._write_via_loop(2000, b'LOOP_WRITE_DD')
        val = self._read_via_loop(2000, 13)
        self.assertEqual(val, b'LOOP_WRITE_DD')

    def test_write_backing_read_loop(self):
        """Write via backing file (os.pwrite), read via loop device (sudo dd).

        If loop device and backing file have separate page caches,
        this test will FAIL (the loop read returns stale data).
        """
        self._write_via_backing(3000, b'BACKING_WRITE_LOOP_READ')
        val = self._read_via_loop(3000, 22)
        self.assertEqual(val, b'BACKING_WRITE_LOOP_READ',
                         'Loop device cache differs from backing file cache')

    def test_write_loop_read_backing(self):
        """Write via loop device (sudo dd), read via backing file (os.pread).

        Symmetric test — both directions must be coherent.
        """
        self._write_via_loop(4000, b'LOOP_WRITE_BACKING_READ')
        val = self._read_via_backing(4000, 24)
        self.assertEqual(val, b'LOOP_WRITE_BACKING_READ',
                         'Backing file cache differs from loop device cache')

    def test_write_loop_sync_read_backing(self):
        """Write via loop device, sync, read via backing file.

        The sync() should flush all caches and guarantee coherence.
        """
        self._write_via_loop(5000, b'LOOP_SYNC_BACKING_READ')
        subprocess.run(['sync'])
        val = self._read_via_backing(5000, 23)
        self.assertEqual(val, b'LOOP_SYNC_BACKING_READ')

    def test_write_backing_sync_read_loop(self):
        """Write via backing file, sync, read via loop device."""
        self._write_via_backing(6000, b'BACKING_SYNC_LOOP_READ')
        subprocess.run(['sync'])
        val = self._read_via_loop(6000, 23)
        self.assertEqual(val, b'BACKING_SYNC_LOOP_READ')


if __name__ == '__main__':
    unittest.main()
