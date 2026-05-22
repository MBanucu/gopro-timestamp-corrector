"""H16: Isolate the exact interleaving that triggers the kernel bug.

Phase C (interleaved exif+fix across 2 mounts) is the only failing phase.
But WHICH operation triggers the writeback that overwrites the DE?

Theories:
1. ExifTool session creation/teardown on mount B triggers writeback on mount A
2. Opening a file via exFAT driver on mount B triggers writeback on mount A
3. The exif metadata write itself triggers writeback on the other mount
4. fix_exfat_raw's backing-file write on mount B triggers writeback on mount A

This test isolates each sub-operation to find the trigger.
"""
from strategies.exfat_raw import ExfatRawIO, ExfatRawFilesystem, ExfatRawOps
from exiftool_session import ExifToolSession
from test.shared import decompress_sparse_image, prepare_sparse_image, \
    setup_loop_device, teardown_loop_device
from datetime import datetime, timezone
import os, sys, threading, unittest
from pathlib import Path

_BD = str(Path(__file__).resolve().parent.parent.parent / 'src')
if _BD not in os.environ.get('PYTHONPATH', ''):
    os.environ.setdefault('PYTHONPATH', _BD)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / 'test'))

FIXED_TS = 1712345678


def _setup(label):
    gz = Path(__file__).parent.parent / 'sdcard.img.gz'
    if not gz.exists():
        raise unittest.SkipTest('sdcard.img.gz not found')
    cached = Path(__file__).parent.parent / 'sdcard.img'
    decompress_sparse_image(gz, cached)
    work, img = prepare_sparse_image(gz, prefix=f'h16_{label}_')
    loop, mnt = setup_loop_device(str(img))
    target = Path(mnt) / 'DCIM' / '100GOPRO'
    files = sorted(target.glob('*.MP4')) or sorted(target.glob('*'))
    ops_list = []
    for f in files:
        io = ExfatRawIO()
        fs = ExfatRawFilesystem(io)
        ops_list.append(ExfatRawOps(io, fs))
    return ops_list, files, loop, mnt, work


def _teardown(loop, mnt, work):
    try: teardown_loop_device(loop, mnt)
    except: pass
    import shutil; shutil.rmtree(work, ignore_errors=True)


def _check_all(ops_list, files, label):
    failures = []
    for i, (ops, f) in enumerate(zip(ops_list, files)):
        raw = ops.read_mtime_raw(str(f))
        if raw != FIXED_TS:
            failures.append(f'{label}[{i}] {f.name}: raw={raw}')
    return failures


def _fix_all(ops_list, files):
    for ops, f in zip(ops_list, files):
        dt = datetime.fromtimestamp(FIXED_TS, tz=timezone.utc)
        ops.fix_exfat_raw(str(f), dt, dry_run=False)


class H16_IsolateTrigger(unittest.TestCase):

    def _run(self, mount_b_operation):
        """Run fix_all on mount A while mount B does *operation*."""
        a = _setup('A'); b = _setup('B')
        try:
            ops_a, files_a, loop_a, mnt_a, work_a = a
            ops_b, files_b, loop_b, mnt_b, work_b = b

            # Phase 1: Exif write ALL files on both mounts
            def exif_all(files):
                with ExifToolSession() as session:
                    for f in files:
                        session.write_embedded(f, datetime.now(timezone.utc))
            threads = [
                threading.Thread(target=exif_all, args=(files_a,)),
                threading.Thread(target=exif_all, args=(files_b,)),
            ]
            for t in threads: t.start()
            for t in threads: t.join()

            # Phase 2: fix ALL files on mount A, while mount B does operation
            failures = []
            def fix_a():
                _fix_all(ops_a, files_a)
                failures.extend(_check_all(ops_a, files_a, 'A'))
            def do_b():
                mount_b_operation(ops_b, files_b, mnt_b)

            threads = [
                threading.Thread(target=fix_a),
                threading.Thread(target=do_b),
            ]
            for t in threads: t.start()
            for t in threads: t.join()

            if failures:
                self.fail(f'{len(failures)} failures:\n' + '\n'.join(failures))
        finally:
            _teardown(*a[2:4], a[4]); _teardown(*b[2:4], b[4])

    def test_trigger_B_exif_write(self):
        """Mount B does exif write on one file while A fixes."""
        def op(ops, files, mnt):
            with ExifToolSession() as session:
                session.write_embedded(files[0], datetime.now(timezone.utc))
        self._run(op)

    def test_trigger_B_exif_write_all(self):
        """Mount B does exif write on ALL files while A fixes."""
        def op(ops, files, mnt):
            with ExifToolSession() as session:
                for f in files:
                    session.write_embedded(f, datetime.now(timezone.utc))
        self._run(op)

    def test_trigger_B_fix_one(self):
        """Mount B fixes one file while A fixes all."""
        def op(ops, files, mnt):
            dt = datetime.fromtimestamp(FIXED_TS, tz=timezone.utc)
            ops[0].fix_exfat_raw(str(files[0]), dt, dry_run=False)
        self._run(op)

    def test_trigger_B_fix_all(self):
        """Mount B fixes ALL files while A fixes all."""
        def op(ops, files, mnt):
            _fix_all(ops, files)
        self._run(op)

    def test_trigger_B_open_file(self):
        """Mount B opens (stat) one file while A fixes."""
        def op(ops, files, mnt):
            os.stat(files[0])
        self._run(op)

    def test_trigger_B_os_getmtime(self):
        """Mount B reads getmtime on one file while A fixes."""
        def op(ops, files, mnt):
            _ = os.path.getmtime(files[0])
        self._run(op)

    def test_trigger_B_read_byte(self):
        """Mount B reads 1 byte from one file while A fixes."""
        def op(ops, files, mnt):
            fd = os.open(str(files[0]), os.O_RDONLY)
            os.read(fd, 1)
            os.close(fd)
        self._run(op)

    def test_trigger_B_touch(self):
        """Mount B touches one file while A fixes."""
        def op(ops, files, mnt):
            import subprocess
            subprocess.run(['touch', '-c', str(files[0])], capture_output=True)
        self._run(op)
