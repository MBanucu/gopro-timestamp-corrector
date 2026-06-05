"""H9 & H11: Project infrastructure tests.

These test the project's own test infrastructure (decompress lock,
mount-point uniqueness) rather than the external exfat-raw library.
"""

import os
import threading
import unittest
from pathlib import Path

_BD = str(Path(__file__).resolve().parent.parent.parent / 'src')
if _BD not in os.environ.get('PYTHONPATH', ''):
    os.environ.setdefault('PYTHONPATH', _BD)

GZ_PATH = Path(__file__).parent.parent / 'sdcard.img.gz'
CACHED = Path(__file__).parent.parent / 'sdcard.img'


def _decompress():
    from test.shared import decompress_sparse_image
    if not GZ_PATH.exists():
        raise unittest.SkipTest('sdcard.img.gz not found')
    decompress_sparse_image(GZ_PATH, CACHED)


class H9_DecompressLockWorks(unittest.TestCase):
    """H9: decompress_sparse_image's flock serializes correctly."""

    def test_parallel_decompress(self):
        _decompress()
        results = {}
        def decompress(label):
            from test.shared import decompress_sparse_image
            try:
                decompress_sparse_image(GZ_PATH, CACHED)
                exists = CACHED.exists()
                results[label] = ('ok', exists, os.path.getsize(CACHED))
            except Exception as e:
                results[label] = ('error', str(e))
        threads = [threading.Thread(target=decompress, args=(l,))
                   for l in ('A', 'B')]
        for t in threads: t.start()
        for t in threads: t.join()
        for label in ('A', 'B'):
            status = results[label]
            self.assertEqual(status[0], 'ok',
                             f'{label}: decompress failed: {status}')
            self.assertTrue(status[1], f'{label}: cached image missing')
            self.assertGreater(status[2], 0, f'{label}: cached image empty')


class H11_MountPointUniqueness(unittest.TestCase):
    """H11: Two parallel mounts get DIFFERENT mount points."""

    def test_parallel_mount_points_differ(self):
        import shutil
        _decompress()
        from test.shared import prepare_sparse_image, setup_loop_device, \
            teardown_loop_device

        results = {}
        def mount_it(label):
            work, img = prepare_sparse_image(GZ_PATH, prefix=f'h11_{label}_')
            try:
                loop, mnt = setup_loop_device(str(img))
                self.addCleanup(teardown_loop_device, loop, mnt)
                self.addCleanup(shutil.rmtree, work, ignore_errors=True)
                results[label] = (loop, mnt, work)
            except Exception as e:
                results[label] = ('error', str(e), work)

        threads = [threading.Thread(target=mount_it, args=(l,))
                   for l in ('A', 'B')]
        for t in threads: t.start()
        for t in threads: t.join()

        for label in ('A', 'B'):
            r = results[label]
            if r[0] == 'error':
                self.fail(f'{label}: mount failed: {r[1]}')
        a_mnt = results['A'][1]
        b_mnt = results['B'][1]
        self.assertNotEqual(
            a_mnt, b_mnt,
            'Both processes mounted at the SAME path!\n'
            f'  A: {results["A"][1]}\n  B: {results["B"][1]}')
