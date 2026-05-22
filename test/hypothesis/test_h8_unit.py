"""H8-unit: Two parallel exFAT mounts — raw mtime stays corrected.

Hypothesis: when two processes independently fix_exfat_raw on
separate exFAT images (different loop devices, different backing
files), each raw mtime stays corrected.  A kernel exFAT driver
bug can cause dirty-inode writeback on one mount to overwrite
the directory entry on the other mount.

This test avoids the full pipeline.  It directly:
1. Creates TWO independent image copies
2. Mounts both via loop devices
3. Dirties an inode on each (touch + sync — flush fails per H5)
4. Fix_exfat_raw on each
5. Verifies raw mtime on each stays corrected

Run with: PYTHONPATH=src python3 -m unittest test.hypothesis.test_h8_unit -v
"""
import os
import subprocess
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

_BD = str(Path(__file__).resolve().parent.parent.parent / 'src')
if _BD not in os.environ.get('PYTHONPATH', ''):
    os.environ.setdefault('PYTHONPATH', _BD)

FIXED_TS = 1712345678


def _setup(test_case):
    """Create + mount one image copy. Returns (ops, loop, mnt, path_to_file, work_dir)."""
    from test.shared import decompress_sparse_image, prepare_sparse_image, \
        setup_loop_device, teardown_loop_device
    gz = Path(__file__).parent.parent / 'sdcard.img.gz'
    if not gz.exists():
        test_case.skipTest('sdcard.img.gz not found')
    cached = Path(__file__).parent.parent / 'sdcard.img'
    decompress_sparse_image(gz, cached)
    work, img = prepare_sparse_image(gz, prefix='h8_unit_')
    try:
        loop, mnt = setup_loop_device(str(img))
    except Exception:
        teardown_loop_device(getattr(test_case, '_loop', None))
        import shutil
        shutil.rmtree(work, ignore_errors=True)
        raise
    target = Path(mnt) / 'DCIM' / '100GOPRO'
    if not target.exists():
        teardown_loop_device(loop, mnt)
        shutil.rmtree(work, ignore_errors=True)
        test_case.skipTest('100GOPRO not found')
    files = sorted(target.glob('*.MP4')) or sorted(target.glob('*'))
    from strategies.exfat_raw import ExfatRawIO, ExfatRawFilesystem, ExfatRawOps
    io = ExfatRawIO()
    fs = ExfatRawFilesystem(io)
    ops = ExfatRawOps(io, fs)
    return ops, loop, mnt, files[0], work


def _teardown(loop, mnt, work):
    from test.shared import teardown_loop_device
    try:
        teardown_loop_device(loop, mnt)
    except Exception:
        pass
    import shutil
    shutil.rmtree(work, ignore_errors=True)


def _raw_mtime(ops, path):
    return ops.read_mtime_raw(str(path))


def _fix_mtime(ops, path, ts=FIXED_TS):
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    ops.fix_exfat_raw(str(path), dt, dry_run=False)


class H8_Unit_TwoParallelMounts(unittest.TestCase):
    """Two parallel exFAT mounts: each raw mtime stays corrected."""

    def test_two_mounts_parallel_fix(self):
        # Set up two mounts
        a = _setup(self)
        b = _setup(self)
        try:
            ops_a, loop_a, mnt_a, file_a, work_a = a
            ops_b, loop_b, mnt_b, file_b, work_b = b

            # Different loop devices?
            self.assertNotEqual(loop_a, loop_b,
                                'Both mounts got the same loop device')

            # Dirty inodes on both mounts
            subprocess.run(['touch', '-c', str(file_a)], capture_output=True)
            subprocess.run(['touch', '-c', str(file_b)], capture_output=True)

            # Fix both in parallel (simulating two concurrent pipelines)
            results = {}
            def fix_and_check(label, ops, f):
                _fix_mtime(ops, f)
                raw = _raw_mtime(ops, f)
                results[label] = raw

            threads = [
                threading.Thread(target=fix_and_check,
                                 args=('A', ops_a, file_a)),
                threading.Thread(target=fix_and_check,
                                 args=('B', ops_b, file_b)),
            ]
            for t in threads: t.start()
            for t in threads: t.join()

            # Verify each file's raw mtime is correct
            for label in ('A', 'B'):
                self.assertEqual(
                    results[label], FIXED_TS,
                    f'Mount {label}: raw mtime was overwritten after '
                    f'parallel fix_exfat_raw')

            # Now simulate verification step: read stat on each
            _ = os.path.getmtime(file_a)
            _ = os.path.getmtime(file_b)
            _ = os.stat(file_a)
            _ = os.stat(file_b)

            # Check again — should still be corrected
            for label, ops, f in [('A', ops_a, file_a), ('B', ops_b, file_b)]:
                raw = _raw_mtime(ops, f)
                self.assertEqual(
                    raw, FIXED_TS,
                    f'Mount {label}: raw mtime changed after stat() — '
                    f'writeback on the OTHER mount may have overwritten it')

        finally:
            for s in (a, b):
                _teardown(s[1], s[2], s[4])
