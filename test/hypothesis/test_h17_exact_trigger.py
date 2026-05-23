"""H17: Exact Phase C trigger — individual ExifTool sessions per file.

Phase C (fails) creates ONE ExifToolSession per file (24 sessions).
H16 (passes) used ONE shared session per mount.  The difference is
the massive parallel load from 24 concurrent exiftool subprocesses.

This test recreates Phase C's exact pattern to verify the trigger.
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
    work, img = prepare_sparse_image(gz, prefix=f'h17_{label}_')
    loop, mnt = setup_loop_device(str(img))
    target = Path(mnt) / 'DCIM' / '100GOPRO'
    files = sorted(target.glob('*.MP4')) or sorted(target.glob('*'))
    ops_list = []
    for f in files:
        io = ExfatRawIO()
        fs = ExfatRawFilesystem(io)
        ops_list.append(ExfatRawOps(io, fs))
    return ops_list, files, loop, mnt, work, img


def _teardown(loop, mnt, work):
    try: teardown_loop_device(loop, mnt)
    except: pass
    import shutil; shutil.rmtree(work, ignore_errors=True)


class H17_ExactPhaseC(unittest.TestCase):

    def test_exact_phase_c_trigger(self):
        """Recreate Phase C exactly: per-file session, both mounts."""
        a = _setup('A'); b = _setup('B')
        try:
            ops_a, files_a, *rest_a = a
            ops_b, files_b, *rest_b = b
            failures = []

            def per_file_pipeline(ops_list, files, label):
                for i, (ops, f) in enumerate(zip(ops_list, files)):
                    # INDIVIDUAL ExifTool session per file
                    with ExifToolSession(connect=None) as session:
                        session.write_embedded(f, datetime.now(timezone.utc))
                    dt = datetime.fromtimestamp(FIXED_TS, tz=timezone.utc)
                    ops.fix_exfat_raw(str(f), dt, dry_run=False)
                    raw = ops.read_mtime_raw(str(f))
                    if raw != FIXED_TS:
                        failures.append(f'{label}[{i}] {f.name}: raw={raw}')

            threads = [
                threading.Thread(target=per_file_pipeline,
                                 args=(ops_a, files_a, 'A')),
                threading.Thread(target=per_file_pipeline,
                                 args=(ops_b, files_b, 'B')),
            ]
            for t in threads: t.start()
            for t in threads: t.join()

            if failures:
                self.fail(f'{len(failures)} failures:\n' + '\n'.join(failures))
        finally:
            _teardown(*a[2:4], a[4]); _teardown(*b[2:4], b[4])
