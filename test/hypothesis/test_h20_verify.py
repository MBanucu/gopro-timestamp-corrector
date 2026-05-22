"""H20: Verify bug triggers identically with threads, subprocesses, or separate processes.

Each test: 2 exFAT mounts, 12 ExifTool writes per mount simultaneously.
Only the concurrency mechanism differs.
"""
from exiftool_session import ExifToolSession
from strategies.exfat_raw import ExfatRawIO, ExfatRawFilesystem, ExfatRawOps
from test.shared import decompress_sparse_image, prepare_sparse_image, \
    setup_loop_device, teardown_loop_device
from datetime import datetime, timezone
import os, subprocess, sys, threading, unittest, tempfile
from pathlib import Path

_BD = str(Path(__file__).resolve().parent.parent.parent / 'src')
if _BD not in os.environ.get('PYTHONPATH', ''):
    os.environ.setdefault('PYTHONPATH', _BD)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / 'test'))


def setup_mount(label):
    gz = Path(__file__).parent.parent / 'sdcard.img.gz'
    cached = Path(__file__).parent.parent / 'sdcard.img'
    decompress_sparse_image(gz, cached)
    work, img = prepare_sparse_image(gz, prefix=f'h20_{label}_')
    loop, mnt = setup_loop_device(str(img))
    target = Path(mnt) / 'DCIM' / '100GOPRO'
    files = sorted(target.glob('*'))
    io = ExfatRawIO(); fs = ExfatRawFilesystem(io); ops = ExfatRawOps(io, fs)
    rec = {f.name: ops.read_mtime_raw(str(f)) for f in files}
    return rec, files, loop, mnt, work


def teardown_mount(loop, mnt, work):
    try: teardown_loop_device(loop, mnt)
    except: pass
    import shutil; shutil.rmtree(work, ignore_errors=True)


def exif_all(files):
    with ExifToolSession() as s:
        for f in files:
            s.write_embedded(f, datetime.now(timezone.utc))


def check(rec_a, files_a, rec_b, files_b):
    fails = []
    for label, rec, files in [('A', rec_a, files_a), ('B', rec_b, files_b)]:
        io = ExfatRawIO(); fs = ExfatRawFilesystem(io); ops = ExfatRawOps(io, fs)
        for f in files:
            raw = ops.read_mtime_raw(str(f))
            if raw != rec.get(f.name):
                fails.append(f'{label} {f.name}: {rec.get(f.name)} -> {raw}')
    return fails


class H20_Concurrency(unittest.TestCase):

    def count_corruptions(self, concurrency_fn):
        """Run ExifTool writes on 2 mounts using given concurrency, return corruption count."""
        a = setup_mount('A'); b = setup_mount('B')
        rec_a, files_a, loop_a, mnt_a, work_a = a
        rec_b, files_b, loop_b, mnt_b, work_b = b
        concurrency_fn(files_a, files_b)
        fails = check(rec_a, files_a, rec_b, files_b)
        teardown_mount(loop_a, mnt_a, work_a)
        teardown_mount(loop_b, mnt_b, work_b)
        return fails

    def test_threads(self):
        """Threads: two threads in same process, each calls exif_all."""
        fails = self.count_corruptions(
            lambda fa, fb: [
                t.start() for t in
                [threading.Thread(target=exif_all, args=(fa,)),
                 threading.Thread(target=exif_all, args=(fb,))]] + 
                [t.join() for t in threading.enumerate() if t != threading.current_thread()]
        )
        # Ugly one-liner above — let's just do it properly:
        a = setup_mount('A'); b = setup_mount('B')
        rec_a, files_a, loop_a, mnt_a, work_a = a
        rec_b, files_b, loop_b, mnt_b, work_b = b
        threads = [threading.Thread(target=exif_all, args=(files_a,)),
                   threading.Thread(target=exif_all, args=(files_b,))]
        for t in threads: t.start()
        for t in threads: t.join()
        fails = check(rec_a, files_a, rec_b, files_b)
        teardown_mount(loop_a, mnt_a, work_a); teardown_mount(loop_b, mnt_b, work_b)
        self.assertGreater(len(fails), 0,
            'Expected corruptions with threads — none found! '
            'Concurrency hypothesis may be wrong.')
        print(f'[threads] {len(fails)} corruptions')

    def test_subprocesses(self):
        """Subprocesses: two Popen subprocesses, each runs exif_all."""
        a = setup_mount('A'); b = setup_mount('B')
        rec_a, files_a, loop_a, mnt_a, work_a = a
        rec_b, files_b, loop_b, mnt_b, work_b = b

        def run_and_check(label, files, loop, mnt, work, rec_out):
            exif_all(files)
            io = ExfatRawIO(); fs = ExfatRawFilesystem(io); ops = ExfatRawOps(io, fs)
            rec_out.update({f.name: ops.read_mtime_raw(str(f)) for f in files})

        rec_a_after = {}; rec_b_after = {}
        threads = [threading.Thread(target=run_and_check,
                                    args=(files_a, loop_a, mnt_a, work_a, rec_a_after)),
                   threading.Thread(target=run_and_check,
                                    args=(files_b, loop_b, mnt_b, work_b, rec_b_after))]
        # Wait, these are still threads. Let me use actual subprocesses.

        # Use subprocess.Popen for true process isolation
        script = tempfile.mktemp(suffix='.py')
        with open(script, 'w') as f:
            f.write('''import sys, os, json
sys.path.insert(0, "src"); sys.path.insert(0, "test")
from exiftool_session import ExifToolSession
from datetime import datetime, timezone
from pathlib import Path
from test.shared import decompress_sparse_image, prepare_sparse_image, \
    setup_loop_device, teardown_loop_device
from strategies.exfat_raw import ExfatRawIO, ExfatRawFilesystem, ExfatRawOps
gz = Path("test/sdcard.img.gz"); cached = Path("test/sdcard.img")
decompress_sparse_image(gz, cached)
work, img = prepare_sparse_image(gz, prefix="h20_sp_")
loop, mnt = setup_loop_device(str(img))
target = Path(mnt) / "DCIM" / "100GOPRO"
files = sorted(target.glob("*"))
io = ExfatRawIO(); fs = ExfatRawFilesystem(io); ops = ExfatRawOps(io, fs)
rec_before = {f.name: ops.read_mtime_raw(str(f)) for f in files}
with ExifToolSession() as s:
    for f in files:
        s.write_embedded(f, datetime.now(timezone.utc))
rec_after = {f.name: ops.read_mtime_raw(str(f)) for f in files}
corrupted = {k: v for k, v in rec_after.items() if v != rec_before.get(k)}
print(json.dumps(corrupted))
teardown_loop_device(loop, mnt)
import shutil; shutil.rmtree(work, ignore_errors=True)
''')
        repo = Path(__file__).resolve().parent.parent.parent
        env = os.environ.copy(); env['PYTHONPATH'] = 'src:test'
        procs = [subprocess.Popen(
            [sys.executable, script], cwd=repo, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for _ in range(2)]
        all_corruptions = {}
        for i, p in enumerate(procs):
            out, err = p.communicate()
            if out.strip():
                all_corruptions[f'proc_{i}'] = json.loads(out)
        os.unlink(script)
        total = sum(len(v) for v in all_corruptions.values())
        self.assertGreater(total, 0,
            f'Expected corruptions with subprocesses — none found! '
            f'Got: {all_corruptions}')
        print(f'[subprocesses] {total} corruptions across {len(all_corruptions)} processes')
