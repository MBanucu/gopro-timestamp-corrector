"""Test: parallel loop-device race due to udisksctl mount-point collision.

Two concurrent ``udisksctl mount`` calls on images with the same volume serial
number can both mount to the same path (e.g. ``/run/media/$USER/7B37-D46E41``).
When that happens, both workers:
  - Operate on the **same** files (the first worker's filesystem)
  - Resolve to the **same** block device (the first worker's ``/dev/loopN``)
  - Write to the **first** worker's backing file

The second worker's raw-block write goes to the first worker's backing file,
and its read-back returns the value *it* wrote — but since both workers share
the same file, the test assertion fails if the second worker's ``os.path.getmtime()``
was called before the first worker's ``os.utime()`` updated the kernel cache.

Moreover, ``_backing_file`` resolves the wrong backing file for the second worker,
so it writes to the first worker's image instead of its own.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

_BD = str(Path(__file__).resolve().parent.parent / 'src')
if _BD not in sys.path:
    sys.path.insert(0, _BD)


def _ops():
    from strategies.exfat_raw import ExfatRawIO, ExfatRawFilesystem, ExfatRawOps
    io = ExfatRawIO()
    return ExfatRawOps(io, ExfatRawFilesystem(io))


class TestParallelLoopRace(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        gz = Path(__file__).parent / 'sdcard.img.gz'
        if not gz.exists():
            raise unittest.SkipTest('sdcard.img.gz not found')
        from test.shared import decompress_sparse_image
        cls._cached = Path(__file__).parent / 'sdcard.img'
        decompress_sparse_image(gz, cls._cached)

    def _worker(self, label: str) -> dict:
        from test.shared import setup_loop_device, teardown_loop_device
        work = Path(tempfile.mkdtemp(prefix=f'race_{label}_'))
        img = work / 'sdcard.img'
        subprocess.run(
            ['cp', '--sparse=always', str(self._cached), str(img)],
            check=True, capture_output=True)
        try:
            loop, mnt = setup_loop_device(str(img))
            target = Path(mnt) / 'DCIM' / '100GOPRO'
            files = sorted(target.glob('*.MP4'))
            if not files:
                files = sorted(target.glob('*'))
            f = files[0]
            orig_ts = int(os.path.getmtime(f))
            new_ts = orig_ts + 3600
            new_dt = datetime.fromtimestamp(new_ts, tz=timezone.utc)
            ops = _ops()
            ops.fix_exfat_raw(str(f), new_dt, dry_run=False)
            raw = ops.read_mtime_raw(str(f))
            ok = raw == new_ts
            return {
                'label': label, 'ok': ok, 'raw': raw or 0, 'exp': new_ts,
                'loop': loop, 'mnt': mnt, 'fpath': str(f),
            }
        except Exception as e:
            return {'label': label, 'ok': False, 'error': str(e)}
        finally:
            import shutil
            try:
                teardown_loop_device(loop, mnt)
            except Exception:
                pass
            shutil.rmtree(work, ignore_errors=True)

    def test_parallel_mount_collision(self):
        failures = []
        mount_collisions = 0
        for run in range(10):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futs = {pool.submit(self._worker, label): label
                        for label in ['A', 'B']}
                results = {}
                for fut in as_completed(futs):
                    r = fut.result()
                    results[r['label']] = r
            a, b = results['A'], results['B']
            if a.get('mnt') == b.get('mnt'):
                mount_collisions += 1
            for r in (a, b):
                if not r.get('ok'):
                    failures.append(
                        f'Run {run}, {r["label"]}: loop={r.get("loop")} '
                        f'mnt={r.get("mnt")} raw={r.get("raw")} exp={r.get("exp")}')
        msg_lines = [
            f'Mount collisions: {mount_collisions}/10 runs',
        ]
        if failures:
            msg_lines.append(f'{len(failures)} worker failure(s):')
            msg_lines.extend(failures)
        print(f'\n  Mount collisions: {mount_collisions}/10 runs')
        if failures:
            print(f'  Worker failures: {len(failures)}')
            for f in failures:
                print(f'    {f}')
        self.assertEqual(
            mount_collisions, 0,
            '\n'.join(msg_lines) +
            '\n\nMount collisions cause workers to operate on the same filesystem.')
        self.assertEqual(
            len(failures), 0,
            '\n'.join(msg_lines) +
            '\n\nRaw-block writes must be visible on read-back under parallel load.')
