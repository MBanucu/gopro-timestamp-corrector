"""H21: Verify individual ExifToolSession per file across threads vs subprocesses.

Both should trigger ~20/24 corruptions (unlike shared session which gets 0).
"""
from exiftool_session import ExifToolSession
from strategies.exfat_raw import ExfatRawIO, ExfatRawFilesystem, ExfatRawOps
from test.shared import decompress_sparse_image, prepare_sparse_image, \
    setup_loop_device, teardown_loop_device
from datetime import datetime, timezone
import os, subprocess, sys, threading, unittest, tempfile, json
from pathlib import Path

_BD = str(Path(__file__).resolve().parent.parent.parent / 'src')
if _BD not in os.environ.get('PYTHONPATH', ''):
    os.environ.setdefault('PYTHONPATH', _BD)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / 'test'))


def setup_mount(label):
    gz = Path(__file__).parent.parent / 'sdcard.img.gz'
    cached = Path(__file__).parent.parent / 'sdcard.img'
    decompress_sparse_image(gz, cached)
    work, img = prepare_sparse_image(gz, prefix=f'h21_{label}_')
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


class H21_IndividualSessions(unittest.TestCase):

    def _individual_exif(self, files):
        """ONE ExifToolSession per file (the trigger pattern)."""
        for f in files:
            with ExifToolSession() as s:
                s.write_embedded(f, datetime.now(timezone.utc))

    def test_threads_individual_sessions(self):
        """Threads: individual sessions per file on 2 mounts."""
        a = setup_mount('A'); b = setup_mount('B')
        rec_a, files_a, loop_a, mnt_a, work_a = a
        rec_b, files_b, loop_b, mnt_b, work_b = b
        threads = [threading.Thread(target=self._individual_exif, args=(files_a,)),
                   threading.Thread(target=self._individual_exif, args=(files_b,))]
        for t in threads: t.start()
        for t in threads: t.join()
        fails = []
        for label, rec, files in [('A', rec_a, files_a), ('B', rec_b, files_b)]:
            io = ExfatRawIO(); fs = ExfatRawFilesystem(io); ops = ExfatRawOps(io, fs)
            for f in files:
                raw = ops.read_mtime_raw(str(f))
                if raw != rec.get(f.name):
                    fails.append(f'{label} {f.name}: {rec.get(f.name)} -> {raw}')
        teardown_mount(loop_a, mnt_a, work_a); teardown_mount(loop_b, mnt_b, work_b)
        self.assertGreater(len(fails), 5,
            f'Expected >5 corruptions with individual sessions (threads), got {len(fails)}')
        print(f'[threads individual] {len(fails)} corruptions')

    def test_subprocesses_individual_sessions(self):
        """Subprocesses: each subprocess uses individual sessions."""
        script = tempfile.mktemp(suffix='.py')
        with open(script, 'w') as f:
            f.write('import sys, os, json\n')
            f.write('print("CWD:", os.getcwd(), flush=True)\n')
            f.write('sys.path.insert(0, "src"); sys.path.insert(0, "test")\n')
            f.write('print("Path0 exists src:", os.path.isdir("src"), flush=True)\n')
            f.write('print("Path0 exists test:", os.path.isdir("test"), flush=True)\n')
            f.write('from exiftool_session import ExifToolSession\n')
            f.write('from datetime import datetime, timezone\n')
            f.write('from pathlib import Path\n')
            f.write('from test.shared import decompress_sparse_image, prepare_sparse_image\n')
            f.write('from test.shared import setup_loop_device, teardown_loop_device\n')
            f.write('from strategies.exfat_raw import ExfatRawIO, ExfatRawFilesystem, ExfatRawOps\n')
            f.write('gz = Path("test/sdcard.img.gz"); cached = Path("test/sdcard.img")\n')
            f.write('decompress_sparse_image(gz, cached)\n')
            f.write('work, img = prepare_sparse_image(gz, prefix="h21_sp_")\n')
            f.write('loop, mnt = setup_loop_device(str(img))\n')
            f.write('target = Path(mnt) / "DCIM" / "100GOPRO"\n')
            f.write('files = sorted(target.glob("*"))\n')
            f.write('io = ExfatRawIO(); fs = ExfatRawFilesystem(io); ops = ExfatRawOps(io, fs)\n')
            f.write('rec_before = {f.name: ops.read_mtime_raw(str(f)) for f in files}\n')
            f.write('for f in files:\n')
            f.write('    with ExifToolSession() as s:\n')
            f.write('        s.write_embedded(f, datetime.now(timezone.utc))\n')
            f.write('rec_after = {f.name: ops.read_mtime_raw(str(f)) for f in files}\n')
            f.write('corrupted = {k: v for k, v in rec_after.items() if v != rec_before.get(k)}\n')
            f.write('print(json.dumps(corrupted))\n')
            f.write('teardown_loop_device(loop, mnt)\n')
            f.write('import shutil; shutil.rmtree(work, ignore_errors=True)\n')
        repo = Path(__file__).resolve().parent.parent.parent
        env = os.environ.copy(); env['PYTHONPATH'] = 'src:test'
        procs = [subprocess.Popen(
            [sys.executable, script], cwd=repo, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for _ in range(2)]
        total = 0
        errors = []
        for i, p in enumerate(procs):
            out, err = p.communicate()
            if err.strip():
                errors.append(f'proc_{i} stderr: {err.strip()[:200]}')
            corr = json.loads(out.strip()) if out.strip() else {}
            total += len(corr)
        os.unlink(script)
        if errors:
            self.fail('Subprocess errors:\n' + '\n'.join(errors))
        self.assertGreater(total, 5,
            f'Expected >5 corruptions with individual sessions (subprocesses), got {total}')
        print(f'[subprocesses individual] {total} corruptions')
