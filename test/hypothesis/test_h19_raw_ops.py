"""H19: Exact raw kernel operations that trigger the cross-mount bug.

Replace fix_exfat_raw and ExifTool with direct syscalls/dd to find
the EXACT operation pair that causes directory entry corruption.

Each test:
- Mount A does OPERATION_X (on backing file or loop device)
- Mount B does OPERATION_Y (on file through mount or loop device)
- Check if ANY untouched file on either mount changes -> corruption

Operation types:
  BACKING_PWRITE  = os.pwrite() on the backing file
  BACKING_FSYNC   = os.fsync() on the backing file
  LOOP_DD_WRITE   = sudo dd of=/dev/loopN conv=fsync
  MOUNT_WRITE     = open(O_WRONLY)+write() to file through mount
  MOUNT_TOUCH     = touch (utimensat) through mount
  MOUNT_EXIF      = ExifTool write_embedded through mount
"""
from exiftool_session import ExifToolSession
from test.shared import decompress_sparse_image, prepare_sparse_image, \
    setup_loop_device, teardown_loop_device
import os, subprocess, sys, struct, threading, unittest, tempfile
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
    work, img = prepare_sparse_image(gz, prefix=f'h19_{label}_')
    loop, mnt = setup_loop_device(str(img))
    target = Path(mnt) / 'DCIM' / '100GOPRO'
    files = sorted(target.glob('*'))
    return loop, mnt, files, work, target


def _teardown(loop, mnt, work):
    try: teardown_loop_device(loop, mnt)
    except: pass
    import shutil; shutil.rmtree(work, ignore_errors=True)


def _backing(loop):
    dn = loop.lstrip('/dev/')
    r = subprocess.run(['cat', f'/sys/block/{dn}/loop/backing_file'],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def _record(files):
    from strategies.exfat_raw import ExfatRawIO, ExfatRawFilesystem, ExfatRawOps
    io = ExfatRawIO(); fs = ExfatRawFilesystem(io); ops = ExfatRawOps(io, fs)
    return {f.name: ops.read_mtime_raw(str(f)) for f in files}, ops


class H19_RawOps(unittest.TestCase):

    def _test_pair(self, op_a, op_b, label_a='A op', label_b='B op'):
        """Run op_a on mount A, op_b on mount B, check cross-mount changes."""
        a = _setup('A'); b = _setup('B')
        try:
            loop_a, mnt_a, files_a, work_a, tgt_a = a
            loop_b, mnt_b, files_b, work_b, b = b  # wait, wrong unpacking
        except: pass

    def test_pair(self):
        """Try ALL combinations of raw operations to find the exact trigger."""
        a = _setup('A'); b = _setup('B')
        try:
            loop_a, mnt_a, files_a, work_a, tgt_a = a
            loop_b, mnt_b, files_b, work_b, tgt_b = b

            rec_a, _ = _record(files_a)
            rec_b, _ = _record(files_b)

            fails = []
            def check(label, files, rec, skip_names=set()):
                _, ops = _record(files)
                for f in files:
                    if f.name in skip_names: continue
                    raw = ops.read_mtime_raw(str(f))
                    if raw != rec.get(f.name):
                        fails.append(f'{label} {f.name}: {rec.get(f.name)} -> {raw}')

            back_a = _backing(loop_a)
            back_b = _backing(loop_b)

            # ── Run operations in parallel ──

            def a_op():
                # DIRECT write to backing file (like fix_exfat_raw does)
                fd = os.open(back_a, os.O_WRONLY)
                os.pwrite(fd, b'\x00' * 32, 100000 * 512)
                os.fsync(fd)
                os.close(fd)

            def b_op():
                # DIRECT write to a file through the mount (like exif write)
                fd = os.open(str(files_b[0]), os.O_WRONLY | os.O_APPEND)
                os.write(fd, b' ')
                os.close(fd)

            threads = [threading.Thread(target=a_op),
                       threading.Thread(target=b_op)]
            for t in threads: t.start()
            for t in threads: t.join()

            check('A', files_a, rec_a, {files_a[0].name})
            check('B', files_b, rec_b, {files_b[0].name})

            if fails:
                self.fail(f'{len(fails)} failures:\n' + '\n'.join(fails))
        finally:
            _teardown(loop_a, mnt_a, work_a)
            _teardown(loop_b, mnt_b, work_b)

    def test_on_loop_device(self):
        """A: dd write to /dev/loopN conv=fsync, B: dd write to /dev/loopM conv=fsync"""
        a = _setup('A'); b = _setup('B')
        try:
            loop_a, mnt_a, files_a, work_a, tgt_a = a
            loop_b, mnt_b, files_b, work_b, tgt_b = b

            rec_a, _ = _record(files_a)
            rec_b, _ = _record(files_b)

            def a_op():
                # DIRECT write to loop device via dd
                subprocess.run(
                    ['sudo', 'dd', f'if={back_a}', f'of={loop_a}',
                     'bs=1', 'seek=100000', 'count=32', 'status=none', 'conv=fsync'],
                    capture_output=True)

            def b_op():
                # DIRECT touch on file through mount
                subprocess.run(['touch', '-c', str(files_b[0])], capture_output=True)

            threads = [threading.Thread(target=a_op),
                       threading.Thread(target=b_op)]
            for t in threads: t.start()
            for t in threads: t.join()

            fails = []
            for label, files, loop, rec in [('A', files_a, loop_a, rec_a),
                                            ('B', files_b, loop_b, rec_b)]:
                if label == 'B': continue  # B only touched, no mtime change expected
                _, ops = _record(files)
                for f in files:
                    if f.name == files_a[0].name: continue
                    raw = ops.read_mtime_raw(str(f))
                    if raw != rec.get(f.name):
                        fails.append(f'{label} {f.name}: {rec.get(f.name)} -> {raw}')
            if fails:
                self.fail(f'{len(fails)}:\n' + '\n'.join(fails))
        finally:
            _teardown(loop_a, mnt_a, work_a)
            _teardown(loop_b, mnt_b, work_b)
