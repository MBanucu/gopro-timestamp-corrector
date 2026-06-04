"""H18c: Minimal kernel operation trigger — proper cross-mount validation.

If a file on mount A changes when only mount B did operations = corruption.
If a file's mtime changes to a value other than what the operation set = corruption.
"""
from exfat_raw import ExfatRawIO, ExfatRawFilesystem, ExfatRawOps
from test.shared import decompress_sparse_image, prepare_sparse_image, \
    setup_loop_device, teardown_loop_device
from datetime import datetime, timezone
import os, struct, subprocess, sys, threading, unittest
from pathlib import Path

_BD = str(Path(__file__).resolve().parent.parent.parent / 'src')
if _BD not in os.environ.get('PYTHONPATH', ''):
    os.environ.setdefault('PYTHONPATH', _BD)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / 'test'))

FIXED_TS = 1712345678


def _setup(test_case, label):
    gz = Path(__file__).parent.parent / 'sdcard.img.gz'
    if not gz.exists(): raise unittest.SkipTest('sdcard.img.gz not found')
    cached = Path(__file__).parent.parent / 'sdcard.img'
    decompress_sparse_image(gz, cached)
    work, img = prepare_sparse_image(gz, prefix=f'h18c_{label}_')
    loop, mnt = setup_loop_device(str(img))
    test_case.addCleanup(teardown_loop_device, loop, mnt)
    import shutil
    test_case.addCleanup(shutil.rmtree, work, ignore_errors=True)
    target = Path(mnt) / 'DCIM' / '100GOPRO'
    files = sorted(target.glob('*.MP4')) or sorted(target.glob('*'))
    io = ExfatRawIO()
    fs = ExfatRawFilesystem(io)
    ops = ExfatRawOps(io, fs)
    return ops, files, loop, mnt, work


def _names(files):
    return {f.name for f in files}


class H18c_KernelTrigger(unittest.TestCase):

    def test_fix_on_A_does_not_affect_B(self):
        """Mount A fixes file[0], mount B does nothing.
        Verify: B's files unchanged, A's other files unchanged."""
        a = _setup(self, 'A'); b = _setup(self, 'B')
        ops_a, files_a, loop_a, mnt_a, work_a = a
        ops_b, files_b, loop_b, mnt_b, work_b = b

        rec_a = {f.name: ops_a.read_mtime_raw(str(f)) for f in files_a}
        rec_b = {f.name: ops_b.read_mtime_raw(str(f)) for f in files_b}

        dt = datetime.fromtimestamp(FIXED_TS, tz=timezone.utc)
        ops_a.fix_exfat_raw(str(files_a[0]), dt, dry_run=False)

        fails = []
        for f in files_a:
            if f.name == files_a[0].name: continue  # expected change
            raw = ops_a.read_mtime_raw(str(f))
            if raw != rec_a[f.name]:
                fails.append(f'A {f.name}: {rec_a[f.name]} -> {raw}')
        for f in files_b:
            raw = ops_b.read_mtime_raw(str(f))
            if raw != rec_b[f.name]:
                fails.append(f'B {f.name}: {rec_b[f.name]} -> {raw}')

        if fails:
            self.fail(f'Corruption: {chr(10)}'.join(fails))

    def test_fix_on_A_plus_exif_on_B(self):
        """A fixes file[0], B touches file[0]. A's other files stay? B's other files?"""
        a = _setup(self, 'A'); b = _setup(self, 'B')
        ops_a, files_a, loop_a, mnt_a, work_a = a
        ops_b, files_b, loop_b, mnt_b, work_b = b

        rec_a = {f.name: ops_a.read_mtime_raw(str(f)) for f in files_a}
        rec_b = {f.name: ops_b.read_mtime_raw(str(f)) for f in files_b}

        fails = []
        def do_a():
            dt = datetime.fromtimestamp(FIXED_TS, tz=timezone.utc)
            ops_a.fix_exfat_raw(str(files_a[0]), dt, dry_run=False)
        def do_b():
            subprocess.run(['touch', '-c', str(files_b[0])], capture_output=True)

        threads = [threading.Thread(target=do_a),
                   threading.Thread(target=do_b)]
        for t in threads: t.start()
        for t in threads: t.join()

        for f in files_a:
            if f.name == files_a[0].name: continue
            raw = ops_a.read_mtime_raw(str(f))
            if raw != rec_a[f.name]:
                fails.append(f'A {f.name}: {rec_a[f.name]} -> {raw}')
        for f in files_b:
            if f.name == files_b[0].name: continue
            raw = ops_b.read_mtime_raw(str(f))
            if raw != rec_b[f.name]:
                fails.append(f'B {f.name}: {rec_b[f.name]} -> {raw}')

        if fails:
            self.fail(f'Corruption: {chr(10)}'.join(fails))

    def test_fix_on_A_plus_fix_on_B(self):
        """A fixes file[0], B fixes file[0]. Other files on both mounts stay?"""
        a = _setup(self, 'A'); b = _setup(self, 'B')
        ops_a, files_a, loop_a, mnt_a, work_a = a
        ops_b, files_b, loop_b, mnt_b, work_b = b

        rec_a = {f.name: ops_a.read_mtime_raw(str(f)) for f in files_a}
        rec_b = {f.name: ops_b.read_mtime_raw(str(f)) for f in files_b}

        def do_a():
            dt = datetime.fromtimestamp(FIXED_TS, tz=timezone.utc)
            ops_a.fix_exfat_raw(str(files_a[0]), dt, dry_run=False)
        def do_b():
            dt = datetime.fromtimestamp(FIXED_TS, tz=timezone.utc)
            ops_b.fix_exfat_raw(str(files_b[0]), dt, dry_run=False)

        threads = [threading.Thread(target=do_a),
                   threading.Thread(target=do_b)]
        for t in threads: t.start()
        for t in threads: t.join()

        fails = []
        for f in files_a:
            if f.name == files_a[0].name: continue
            raw = ops_a.read_mtime_raw(str(f))
            if raw != rec_a[f.name]:
                fails.append(f'A {f.name}: {rec_a[f.name]} -> {raw}')
        for f in files_b:
            if f.name == files_b[0].name: continue
            raw = ops_b.read_mtime_raw(str(f))
            if raw != rec_b[f.name]:
                fails.append(f'B {f.name}: {rec_b[f.name]} -> {raw}')

        if fails:
            self.fail(f'Corruption: {chr(10)}'.join(fails))
