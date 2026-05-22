"""H9-12: Unit tests for specific parallel failure hypotheses.

Each test verifies a focused hypothesis about WHY two parallel
test_full_auto_integration pipelines fail (~50% of the time).

H9: decompress_sparse_image lock works across subprocesses
H10: _backing_file resolves correctly for both loop devices in parallel
H11: Both processes mount to different mount points
H12: read_mtime_raw returns None or wrong value during parallel execution
"""
import os
import subprocess
import sys
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
    """H9: decompress_sparse_image's flock serializes correctly.

    Two parallel calls should both return with the cached image present.
    """
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


class H10_BackingFileResolution(unittest.TestCase):
    """H10: _backing_file resolves correctly for parallel loop devices.

    Two parallel setup_loop_device calls should each resolve to
    their OWN backing file path, never the other's.
    """
    def test_parallel_backing_resolution(self):
        _decompress()
        from test.shared import prepare_sparse_image, setup_loop_device, \
            teardown_loop_device
        from strategies.exfat_raw import ExfatRawIO
        import tempfile, shutil

        results = {}
        def setup_and_resolve(label):
            work, img = prepare_sparse_image(GZ_PATH, prefix=f'h10_{label}_')
            try:
                loop, mnt = setup_loop_device(str(img))
                io = ExfatRawIO()
                backing = io._backing_file(loop)
                results[label] = (loop, mnt, backing, str(img), work)
            except Exception as e:
                results[label] = ('error', str(e), work)

        threads = [threading.Thread(target=setup_and_resolve, args=(l,))
                   for l in ('A', 'B')]
        for t in threads: t.start()
        for t in threads: t.join()

        for label in ('A', 'B'):
            r = results[label]
            if r[0] == 'error':
                shutil.rmtree(r[2], ignore_errors=True)
                self.fail(f'{label}: setup failed: {r[1]}')
            loop, mnt, backing, img_path, work = r
            # Different loop devices?
            other = results['B' if label == 'A' else 'A']
            if other[0] != 'error':
                self.assertNotEqual(
                    loop, other[0],
                    f'{label}: same loop device as other!')
            # Backing file matches our image?
            self.assertEqual(
                backing, img_path,
                f'{label}: backing_file ({backing}) != our image ({img_path})')
            # Cleanup
            teardown_loop_device(loop, mnt)
            shutil.rmtree(work, ignore_errors=True)


class H11_MountPointUniqueness(unittest.TestCase):
    """H11: Two parallel mounts get DIFFERENT mount points.

    If both mount to the same path, they'd share the same filesystem.
    """
    def test_parallel_mount_points_differ(self):
        _decompress()
        from test.shared import prepare_sparse_image, setup_loop_device, \
            teardown_loop_device
        import shutil

        results = {}
        def mount_it(label):
            work, img = prepare_sparse_image(GZ_PATH, prefix=f'h11_{label}_')
            try:
                loop, mnt = setup_loop_device(str(img))
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

        for label in ('A', 'B'):
            loop, mnt, work = results[label]
            teardown_loop_device(loop, mnt)
            shutil.rmtree(work, ignore_errors=True)


class H12_RawMtimeDuringParallel(unittest.TestCase):
    """H12: read_mtime_raw returns the correct value during parallel
    exif_write + fix_exfat_raw pipelines.

    Two processes each:
    1. Mount their own image
    2. Modify a file (dirties inode)
    3. fix_exfat_raw
    4. read_mtime_raw

    If read_mtime_raw returns None or the wrong value, the parallel
    interference hypothesis is confirmed.
    """
    def test_parallel_raw_read_after_fix(self):
        _decompress()
        from test.shared import prepare_sparse_image, setup_loop_device, \
            teardown_loop_device
        from strategies.exfat_raw import ExfatRawIO, ExfatRawFilesystem, \
            ExfatRawOps
        from datetime import datetime, timezone
        import shutil

        results = {}
        def run_pipeline(label):
            work, img = prepare_sparse_image(GZ_PATH, prefix=f'h12_{label}_')
            try:
                loop, mnt = setup_loop_device(str(img))
                target = Path(mnt) / 'DCIM' / '100GOPRO'
                files = sorted(target.glob('*.MP4')) or sorted(target.glob('*'))
                f = files[0]

                # Simulate exif write: modify file to dirty inode
                subprocess.run(['touch', '-c', str(f)], capture_output=True)

                # fix_exfat_raw
                io = ExfatRawIO()
                fs = ExfatRawFilesystem(io)
                ops = ExfatRawOps(io, fs)
                target_ts = 1712345678
                dt = datetime.fromtimestamp(target_ts, tz=timezone.utc)
                ops.fix_exfat_raw(str(f), dt, dry_run=False)

                # read_mtime_raw
                raw = ops.read_mtime_raw(str(f))
                results[label] = ('ok', raw, target_ts, loop, mnt, work)
            except Exception as e:
                results[label] = ('error', str(e), None, None, None, work)

        threads = [threading.Thread(target=run_pipeline, args=(l,))
                   for l in ('A', 'B')]
        for t in threads: t.start()
        for t in threads: t.join()

        for label in ('A', 'B'):
            r = results[label]
            status = r[0]
            if status == 'error':
                shutil.rmtree(r[5], ignore_errors=True)
                self.fail(f'{label}: pipeline error: {r[1]}')
            raw, expected = r[1], r[2]
            loop, mnt = r[3], r[4]
            self.assertIsNotNone(
                raw,
                f'{label}: read_mtime_raw returned None after fix!')
            self.assertEqual(
                raw, expected,
                f'{label}: read_mtime_raw returned {raw}, '
                f'expected {expected}')
            teardown_loop_device(loop, mnt)
            shutil.rmtree(r[5], ignore_errors=True)
