"""H13: Parallel exif metadata write + fix_exfat_raw + read_mtime_raw.

The full pipeline uses ExifTool to write embedded metadata, which
dirties the inode differently than touch.  This test uses the real
ExifTool session to verify parallel isolation.
"""
import os
import subprocess
import sys
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

_BD = str(Path(__file__).resolve().parent.parent.parent / 'src')
if _BD not in os.environ.get('PYTHONPATH', ''):
    os.environ.setdefault('PYTHONPATH', _BD)


def _setup(test_case):
    """Setup one image copy. Returns (ops, loop, mnt, file, work)."""
    from test.shared import decompress_sparse_image, prepare_sparse_image, \
        setup_loop_device, teardown_loop_device
    from strategies.exfat_raw import ExfatRawIO, ExfatRawFilesystem, ExfatRawOps
    gz = Path(__file__).parent.parent / 'sdcard.img.gz'
    if not gz.exists():
        test_case.skipTest('sdcard.img.gz not found')
    cached = Path(__file__).parent.parent / 'sdcard.img'
    decompress_sparse_image(gz, cached)
    work, img = prepare_sparse_image(gz, prefix='h13_')
    try:
        loop, mnt = setup_loop_device(str(img))
    except Exception:
        teardown_loop_device(getattr(test_case, '_loop', None))
        import shutil; shutil.rmtree(work, ignore_errors=True)
        raise
    target = Path(mnt) / 'DCIM' / '100GOPRO'
    if not target.exists():
        teardown_loop_device(loop, mnt)
        shutil.rmtree(work, ignore_errors=True)
        test_case.skipTest('100GOPRO not found')
    files = sorted(target.glob('*.MP4')) or sorted(target.glob('*'))
    f = files[0]
    io = ExfatRawIO()
    fs = ExfatRawFilesystem(io)
    ops = ExfatRawOps(io, fs)
    return ops, loop, mnt, f, work


def _teardown(loop, mnt, work):
    from test.shared import teardown_loop_device
    try: teardown_loop_device(loop, mnt)
    except: pass
    import shutil; shutil.rmtree(work, ignore_errors=True)


class H13_ParallelWithExifTool(unittest.TestCase):
    """Parallel pipelines using real ExifTool metadata write."""

    def test_parallel_exif_then_fix_then_verify(self):
        a = _setup(self)
        b = _setup(self)
        try:
            ops_a, loop_a, mnt_a, file_a, work_a = a
            ops_b, loop_b, mnt_b, file_b, work_b = b

            from exiftool_session import ExifToolSession
            results = {}

            def pipeline(label, ops, f):
                try:
                    # Step 1: Write embedded metadata via exiftool (dirties inode)
                    with ExifToolSession() as session:
                        session.write_embedded(f, datetime.now(timezone.utc))

                    # Step 2: Fix mtime via raw block
                    target_ts = 1712345678
                    dt = datetime.fromtimestamp(target_ts, tz=timezone.utc)
                    ops.fix_exfat_raw(str(f), dt, dry_run=False)

                    # Step 3: Read back raw mtime
                    raw = ops.read_mtime_raw(str(f))
                    results[label] = ('ok', raw, target_ts)
                except Exception as e:
                    results[label] = ('error', str(e))

            threads = [
                threading.Thread(target=pipeline, args=('A', ops_a, file_a)),
                threading.Thread(target=pipeline, args=('B', ops_b, file_b)),
            ]
            for t in threads: t.start()
            for t in threads: t.join()

            for label in ('A', 'B'):
                r = results.get(label)
                if r is None:
                    self.fail(f'{label}: no result')
                if r[0] == 'error':
                    self.fail(f'{label}: error: {r[1]}')
                raw, expected = r[1], r[2]
                self.assertIsNotNone(raw,
                    f'{label}: read_mtime_raw returned None!')
                self.assertEqual(raw, expected,
                    f'{label}: read_mtime_raw={raw} expected={expected}.')
        finally:
            _teardown(*a[1:3], a[4])
            _teardown(*b[1:3], b[4])
