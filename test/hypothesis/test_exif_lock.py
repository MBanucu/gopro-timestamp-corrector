"""Verify the exiftool write lock prevents cross-mount corruption.

H24a: lock serializes batch writes — two threads share one lock file.
H24b: lock prevents subprocess corruption — two processes with individual
      sessions each writing one batch should NOT corrupt DEs (with lock).
H24c: Writer pipeline in parallel — two test_full_auto_integration
      pipelines run simultaneously should pass.
"""
from exiftool_session import ExifToolSession, EXIFTOOL_WRITE_LOCK
from strategies.exfat_raw import ExfatRawIO, ExfatRawFilesystem, ExfatRawOps
from test.shared import decompress_sparse_image, prepare_sparse_image, \
    setup_loop_device, teardown_loop_device
from datetime import datetime, timezone
import fcntl, os, subprocess, sys, threading, unittest, json, tempfile
from pathlib import Path

_BD = str(Path(__file__).resolve().parent.parent.parent / 'src')
if _BD not in os.environ.get('PYTHONPATH', ''):
    os.environ.setdefault('PYTHONPATH', _BD)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / 'test'))


def _setup(label):
    gz = Path(__file__).parent.parent / 'sdcard.img.gz'
    if not gz.exists(): raise unittest.SkipTest('sdcard.img.gz not found')
    cached = Path(__file__).parent.parent / 'sdcard.img'
    decompress_sparse_image(gz, cached)
    work, img = prepare_sparse_image(gz, prefix=f'h24_{label}_')
    loop, mnt = setup_loop_device(str(img))
    target = Path(mnt) / 'DCIM' / '100GOPRO'
    files = sorted(target.glob('*'))
    io = ExfatRawIO(); fs = ExfatRawFilesystem(io); ops = ExfatRawOps(io, fs)
    rec = {f.name: ops.read_mtime_raw(str(f)) for f in files}
    return rec, files, loop, mnt, work


def _teardown(loop, mnt, work):
    try: teardown_loop_device(loop, mnt)
    except: pass
    import shutil; shutil.rmtree(work, ignore_errors=True)


def _check(rec_a, files_a, rec_b, files_b):
    fails = []
    for label, rec, files in [('A', rec_a, files_a), ('B', rec_b, files_b)]:
        io = ExfatRawIO(); fs = ExfatRawFilesystem(io); ops = ExfatRawOps(io, fs)
        for f in files:
            raw = ops.read_mtime_raw(str(f))
            if raw != rec.get(f.name):
                fails.append(f'{label} {f.name}: {rec.get(f.name)} -> {raw}')
    return fails


class H24_LockVerification(unittest.TestCase):

    def test_lock_serializes_threads(self):
        """Two threads sharing one lock file execute sequentially."""
        import time
        order = []
        def worker(label):
            with open(EXIFTOOL_WRITE_LOCK, 'w') as lk:
                fcntl.flock(lk, fcntl.LOCK_EX)
                order.append(label)
                time.sleep(0.02)
        threads = [threading.Thread(target=worker, args=(f't{i}',))
                   for i in range(5)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len(order), 5, 'All 5 workers must run')
        self.assertEqual(order, sorted(order),
                         f'Lock failed to serialize: order={order}')

    def test_batch_write_no_corruption(self):
        """Batch write on 2 mounts in threads — with lock, no corruption."""
        a = _setup('A'); b = _setup('B')
        rec_a, files_a, loop_a, mnt_a, work_a = a
        rec_b, files_b, loop_b, mnt_b, work_b = b

        def batch(label, files):
            pairs = [(f, datetime.now(timezone.utc)) for f in files]
            with ExifToolSession(connect=None) as s:
                ok = s.write_embedded_batch(pairs)
            self.assertTrue(ok, f'{label} batch write failed')

        threads = [threading.Thread(target=batch, args=('A', files_a)),
                   threading.Thread(target=batch, args=('B', files_b))]
        for t in threads: t.start()
        for t in threads: t.join()

        fails = _check(rec_a, files_a, rec_b, files_b)
        _teardown(loop_a, mnt_a, work_a); _teardown(loop_b, mnt_b, work_b)
        self.assertEqual(len(fails), 0,
                         f'Corruption with lock: {len(fails)}\n' + '\n'.join(fails[:5]))

    def test_subprocess_batch_no_corruption(self):
        """Two subprocesses each writing a batch — with lock, no corruption."""
        script = tempfile.mktemp(suffix='.py')
        with open(script, 'w') as f:
            f.write('import sys, json\n')
            f.write('sys.path.insert(0, "' + str(Path(__file__).resolve().parent.parent.parent) + '")\n')
            f.write('from exiftool_session import ExifToolSession\n')
            f.write('from datetime import datetime, timezone\n')
            f.write('from pathlib import Path\n')
            f.write('from test.shared import decompress_sparse_image, prepare_sparse_image\n')
            f.write('from test.shared import setup_loop_device, teardown_loop_device\n')
            f.write('from strategies.exfat_raw import ExfatRawIO, ExfatRawFilesystem, ExfatRawOps\n')
            f.write('gz = Path("test/sdcard.img.gz"); cached = Path("test/sdcard.img")\n')
            f.write('decompress_sparse_image(gz, cached)\n')
            f.write('work, img = prepare_sparse_image(gz, prefix="h24_sp_")\n')
            f.write('loop, mnt = setup_loop_device(str(img))\n')
            f.write('target = Path(mnt) / "DCIM" / "100GOPRO"\n')
            f.write('files = sorted(target.glob("*"))\n')
            f.write('io = ExfatRawIO(); fs = ExfatRawFilesystem(io); ops = ExfatRawOps(io, fs)\n')
            f.write('before = {f.name: ops.read_mtime_raw(str(f)) for f in files}\n')
            f.write('pairs = [(f, datetime.now(timezone.utc)) for f in files]\n')
            f.write('with ExifToolSession(connect=None) as s:\n')
            f.write('    ok = s.write_embedded_batch(pairs)\n')
            f.write('after = {f.name: ops.read_mtime_raw(str(f)) for f in files}\n')
            f.write('corrupted = {k: v for k, v in after.items() if v != before.get(k)}\n')
            f.write('print(json.dumps(corrupted), flush=True)\n')
            f.write('teardown_loop_device(loop, mnt)\n')
            f.write('import shutil; shutil.rmtree(work, ignore_errors=True)\n')

        repo = Path(__file__).resolve().parent.parent.parent
        env = os.environ.copy()
        procs = [subprocess.Popen(
            [sys.executable, script], cwd=repo, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for _ in range(2)]
        total = 0
        errors = []
        for i, p in enumerate(procs):
            out, err = p.communicate(timeout=120)
            if err.strip(): errors.append(f'proc_{i}: {err.strip()[:200]}')
            if out.strip():
                corr = json.loads(out.strip())
                total += len(corr)
        os.unlink(script)
        if errors: self.fail('\n'.join(errors))
        self.assertEqual(total, 0,
                         f'Batch lock FAILED: {total} corruptions across 2 subprocesses')

    def test_parallel_pipeline_passes(self):
        """Two test_full_auto_integration pipelines in parallel must both pass."""
        repo = Path(__file__).resolve().parent.parent.parent
        env = os.environ.copy(); env['PYTHONPATH'] = 'src:test'
        count = 1  # single run, 2 parallel pipelines
        for run in range(count):
            procs = [subprocess.Popen(
                [sys.executable, '-m', 'unittest',
                 'test_full_auto_integration.TestFullAutoIntegration.test_full_pipeline',
                 '-v'],
                cwd=repo, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                for _ in range(2)]
            results = []
            for p in procs:
                out, _ = p.communicate(timeout=120)
                passed = 'FAILED' not in out and 'FAIL' not in out.split('\n')[-3]
                results.append(passed)
            self.assertTrue(all(results),
                            f'Run {run}: parallel pipelines FAILED (both must pass)')
