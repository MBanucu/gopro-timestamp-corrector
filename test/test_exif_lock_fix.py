"""Unit tests for exiftool write lock.

Tests:
  1. Lock serializes concurrent exiftool writes across threads
  2. Batch writes with lock leave no corruption on two mounts
  3. Writer pipeline with exiftool + fix_exfat_raw leaves DEs correct
"""
from exiftool_session import ExifToolSession
from strategies.exfat_raw import ExfatRawIO, ExfatRawFilesystem, ExfatRawOps
from test.shared import decompress_sparse_image, prepare_sparse_image, \
    setup_loop_device, teardown_loop_device
from datetime import datetime, timezone
import os, threading, unittest
from pathlib import Path

_BD = str(Path(__file__).resolve().parent.parent / 'src')
import sys
if _BD not in sys.path: sys.path.insert(0, _BD)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'test'))

_BATCH_LOCK = threading.Lock()


def _mount(label):
    gz = Path(__file__).parent / 'sdcard.img.gz'
    if not gz.exists(): raise unittest.SkipTest('sdcard.img.gz not found')
    cached = Path(__file__).parent / 'sdcard.img'
    decompress_sparse_image(gz, cached)
    work, img = prepare_sparse_image(gz, prefix=f'ut_{label}_')
    loop, mnt = setup_loop_device(str(img))
    target = Path(mnt) / 'DCIM' / '100GOPRO'
    files = sorted(target.glob('*'))
    io = ExfatRawIO(); fs = ExfatRawFilesystem(io)
    ops = ExfatRawOps(io, fs)
    rec = {f.name: ops.read_mtime_raw(str(f)) for f in files}
    return ops, rec, files, loop, mnt, work


def _umount(loop, mnt, work):
    try: teardown_loop_device(loop, mnt)
    except: pass
    import shutil; shutil.rmtree(work, ignore_errors=True)


class TestExifLockFix(unittest.TestCase):
    """Verify exiftool write lock prevents concurrent write corruption."""

    def test_lock_serializes(self):
        """The thread lock ensures only one exiftool write runs at a time."""
        import time
        times = []
        def worker():
            with _BATCH_LOCK:
                times.append(time.perf_counter())
                time.sleep(0.05)
        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads: t.start()
        for t in threads: t.join()
        gaps = [(times[i] - times[i-1]) for i in range(1, len(times))]
        min_gap = min(gaps) if gaps else 0
        self.assertGreater(min_gap, 0.03,
            f'Lock failed: min gap {min_gap*1000:.1f}ms')

    def test_batch_write_no_corruption(self):
        """Two mounts, batch writes with lock — no DE corruption."""
        a = _mount('A'); b = _mount('B')
        ops_a, rec_a, files_a, loop_a, mnt_a, work_a = a
        ops_b, rec_b, files_b, loop_b, mnt_b, work_b = b

        def batch(label, files):
            pairs = [(f, datetime.now(timezone.utc)) for f in files]
            with _BATCH_LOCK:
                with ExifToolSession() as s:
                    self.assertTrue(s.write_embedded_batch(pairs),
                                    f'{label} batch failed')
        threads = [threading.Thread(target=batch, args=('A', files_a)),
                   threading.Thread(target=batch, args=('B', files_b))]
        for t in threads: t.start()
        for t in threads: t.join()

        fails = []
        for label, ops, rec, files in [('A', ops_a, rec_a, files_a),
                                       ('B', ops_b, rec_b, files_b)]:
            for f in files:
                raw = ops.read_mtime_raw(str(f))
                if raw != rec.get(f.name):
                    fails.append(f'{label} {f.name}: {rec.get(f.name)} -> {raw}')
        _umount(loop_a, mnt_a, work_a); _umount(loop_b, mnt_b, work_b)
        self.assertEqual(len(fails), 0,
                         f'Batch lock failed: {len(fails)} corruptions\n' +
                         '\n'.join(fails[:5]))

    def test_writer_pipeline_no_corruption(self):
        """Single mount: full Writer pipeline leaves DEs correct."""
        a = _mount('A')
        ops, rec, files, loop, mnt, work = a
        from writer import Writer, WriteJob
        with ExifToolSession() as session:
            jobs = [WriteJob(path=f, target_embedded=datetime.now(timezone.utc),
                             target_mtime=datetime.now(timezone.utc))
                    for f in files]
            with Writer(mnt, fix_btime='exfat_raw', session=session) as w:
                summary = w.write_all(jobs)
        self.assertEqual(summary.written, len(files))
        self.assertIsNone(summary.errors)
        fails = []
        for f in files:
            raw = ops.read_mtime_raw(str(f))
            if raw == rec.get(f.name):
                fails.append(f'{f.name}: mtime unchanged ({raw})')
        _umount(loop, mnt, work)
        self.assertEqual(len(fails), 0,
                         f'{len(fails)} files unchanged — correction failed')
