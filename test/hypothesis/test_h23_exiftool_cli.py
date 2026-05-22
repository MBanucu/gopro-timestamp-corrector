"""H23: Does the bug reproduce with command-line exiftool (not Python library)?

Python ExifToolSession uses a persistent daemon (exiftool -stay_open).
Command-line exiftool spawns a new process for each file.

If both trigger the bug, the trigger is the exiftool write pattern itself.
If only the Python library triggers it, the daemon's persistent connection matters.
"""
from exiftool_session import ExifToolSession
from strategies.exfat_raw import ExfatRawIO, ExfatRawFilesystem, ExfatRawOps
from test.shared import decompress_sparse_image, prepare_sparse_image, \
    setup_loop_device, teardown_loop_device
from datetime import datetime, timezone
import os, subprocess, sys, threading, unittest
from pathlib import Path

_BD = str(Path(__file__).resolve().parent.parent.parent / 'src')
if _BD not in os.environ.get('PYTHONPATH', ''):
    os.environ.setdefault('PYTHONPATH', _BD)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / 'test'))


def setup_mount(label):
    gz = Path(__file__).parent.parent / 'sdcard.img.gz'
    cached = Path(__file__).parent.parent / 'sdcard.img'
    decompress_sparse_image(gz, cached)
    work, img = prepare_sparse_image(gz, prefix=f'h23_{label}_')
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


def check(rec_a, files_a, rec_b, files_b):
    fails = []
    for label, rec, files in [('A', rec_a, files_a), ('B', rec_b, files_b)]:
        io = ExfatRawIO(); fs = ExfatRawFilesystem(io); ops = ExfatRawOps(io, fs)
        for f in files:
            raw = ops.read_mtime_raw(str(f))
            if raw != rec.get(f.name):
                fails.append(f'{label} {f.name}: {rec.get(f.name)} -> {raw}')
    return fails


class H23_CLIvsLibrary(unittest.TestCase):

    def test_command_line_exiftool(self):
        """Command-line exiftool spawns a new process per file: subprocess.run(['exiftool', ...])."""
        a = setup_mount('A'); b = setup_mount('B')
        rec_a, files_a, loop_a, mnt_a, work_a = a
        rec_b, files_b, loop_b, mnt_b, work_b = b

        def run_cli(files):
            for f in files:
                dt = datetime.now(timezone.utc).strftime('%Y:%m:%d %H:%M:%S')
                subprocess.run(
                    ['exiftool', '-overwrite_original',
                     f'-QuickTime:CreateDate={dt}',
                     f'-QuickTime:ModifyDate={dt}',
                     f'-QuickTime:TrackCreateDate={dt}',
                     f'-QuickTime:TrackModifyDate={dt}',
                     str(f)],
                    capture_output=True)

        threads = [threading.Thread(target=run_cli, args=(files_a,)),
                   threading.Thread(target=run_cli, args=(files_b,))]
        for t in threads: t.start()
        for t in threads: t.join()

        fails = check(rec_a, files_a, rec_b, files_b)
        teardown_mount(loop_a, mnt_a, work_a)
        teardown_mount(loop_b, mnt_b, work_b)
        self.assertGreater(
            len(fails), 0,
            f'Command-line exiftool: 0 corruptions (expected >0)')
        print(f'[CLI exiftool] {len(fails)} corruptions')

    def test_python_library_individual(self):
        """Python ExifToolSession, individual session per file (the known trigger)."""
        a = setup_mount('A'); b = setup_mount('B')
        rec_a, files_a, loop_a, mnt_a, work_a = a
        rec_b, files_b, loop_b, mnt_b, work_b = b

        def run_lib(files):
            for f in files:
                with ExifToolSession() as s:
                    s.write_embedded(f, datetime.now(timezone.utc))

        threads = [threading.Thread(target=run_lib, args=(files_a,)),
                   threading.Thread(target=run_lib, args=(files_b,))]
        for t in threads: t.start()
        for t in threads: t.join()

        fails = check(rec_a, files_a, rec_b, files_b)
        teardown_mount(loop_a, mnt_a, work_a)
        teardown_mount(loop_b, mnt_b, work_b)
        self.assertGreater(
            len(fails), 0,
            f'Python library individual sessions: 0 corruptions (expected >0)')
        print(f'[Python lib individual] {len(fails)} corruptions')
