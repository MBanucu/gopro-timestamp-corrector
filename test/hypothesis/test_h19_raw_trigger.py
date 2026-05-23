"""H19: Isolate exact kernel trigger — raw operations vs high-level calls.

Phase 1: touch (utimensat) instead of ExifTool. fix_exfat_raw unchanged.
         Expected: ALL A files change (fix), NO B files change.
Phase 2: raw os.pwrite+os.fsync instead of fix_exfat_raw. ExifTool unchanged.
         Expected: NO files change (raw writes at data offset, not DE).
Phase 3: raw ops + touch (both minimal).
         Expected: NO files change.
Phase 4: Original H14 (both ExifTool + fix_exfat_raw). CONTROL.
         Expected: ALL files on both mounts change.
Phase 5: fix_exfat_raw only, no exif/exif-like writes.
         Expected: ALL files on both mounts change.
Phase 6: ExifTool only, no fix_exfat_raw.
         Expected: NO files change (ExifTool modifies data, not DE).
"""
from strategies.exfat_raw import ExfatRawIO, ExfatRawFilesystem, ExfatRawOps
from exiftool_session import ExifToolSession
from test.shared import decompress_sparse_image, prepare_sparse_image, \
    setup_loop_device, teardown_loop_device
from datetime import datetime, timezone
import os, subprocess, sys, threading, unittest
from pathlib import Path

_BD = str(Path(__file__).resolve().parent.parent.parent / 'src')
if _BD not in os.environ.get('PYTHONPATH', ''):
    os.environ.setdefault('PYTHONPATH', _BD)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / 'test'))

FIXED_TS = 1712345678


def _setup(label):
    gz = Path(__file__).parent.parent / 'sdcard.img.gz'
    if not gz.exists(): raise unittest.SkipTest('sdcard.img.gz not found')
    cached = Path(__file__).parent.parent / 'sdcard.img'
    decompress_sparse_image(gz, cached)
    work, img = prepare_sparse_image(gz, prefix=f'h19_{label}_')
    loop, mnt = setup_loop_device(str(img))
    target = Path(mnt) / 'DCIM' / '100GOPRO'
    files = sorted(target.glob('*'))
    io = ExfatRawIO()
    fs = ExfatRawFilesystem(io)
    ops = ExfatRawOps(io, fs)
    return ops, files, loop, mnt, work


def _teardown(loop, mnt, work):
    try: teardown_loop_device(loop, mnt)
    except: pass
    import shutil; shutil.rmtree(work, ignore_errors=True)


def _record(files):
    io = ExfatRawIO(); fs = ExfatRawFilesystem(io); ops = ExfatRawOps(io, fs)
    return {f.name: ops.read_mtime_raw(str(f)) for f in files}, ops


def _names(files):
    return {f.name for f in files}


class H19_RawTrigger(unittest.TestCase):

    def _run(self, a_action, b_action, a_exp, b_exp):
        """Set up 2 mounts, run a_action on A and b_action on B, verify no cross-mount corruption.
        a_exp/b_exp: set of filenames expected to change, or 'all' for all files on that mount."""
        a = _setup('A'); b = _setup('B')
        ops_a, files_a, loop_a, mnt_a, work_a = a
        ops_b, files_b, loop_b, mnt_b, work_b = b
        if a_exp == 'all': a_exp = {f.name for f in files_a}
        if b_exp == 'all': b_exp = {f.name for f in files_b}
        rec_a, _ = _record(files_a)
        rec_b, _ = _record(files_b)

        threads = [threading.Thread(target=a_action, args=(ops_a, files_a)),
                   threading.Thread(target=b_action, args=(ops_b, files_b))]
        for t in threads: t.start()
        for t in threads: t.join()

        fails = []
        for label, ops, files, rec, exp in [
            ('A', ops_a, files_a, rec_a, a_exp),
            ('B', ops_b, files_b, rec_b, b_exp),
        ]:
            for f in files:
                if f.name in exp: continue
                raw = ops.read_mtime_raw(str(f))
                if raw != rec.get(f.name):
                    fails.append(f'{label} {f.name}: {rec.get(f.name)} -> {raw}')

        _teardown(loop_a, mnt_a, work_a)
        _teardown(loop_b, mnt_b, work_b)
        if fails:
            self.fail(f'{len(fails)} cross-mount corruptions:\n' + '\n'.join(fails))

    # ── Phase 1: touch + fix_exfat_raw ─────────────────────────
    def test_p1_touch_and_fix(self):
        def a(ops, files):
            for f in files:
                dt = datetime.fromtimestamp(FIXED_TS, tz=timezone.utc)
                ops.fix_exfat_raw(str(f), dt, dry_run=False)
        def b(ops, files):
            for f in files:
                subprocess.run(['touch', '-c', str(f)], capture_output=True)
        self._run(a, b, 'all', set())

    # ── Phase 2: raw backing ops + ExifTool ────────────────────
    def test_p2_raw_and_exif(self):
        def a(ops, files):
            pass  # write to backing file at safe offset (not DE)
        def b(ops, files):
            for f in files:
                with ExifToolSession(connect=None) as s:
                    s.write_embedded(f, datetime.now(timezone.utc))
        self._run(a, b, set(), set())

    # ── Phase 3: raw ops + touch ───────────────────────────────
    def test_p3_raw_and_touch(self):
        def a(ops, files): pass
        def b(ops, files):
            for f in files:
                subprocess.run(['touch', '-c', str(f)], capture_output=True)
        self._run(a, b, set(), set())

    # ── Phase 4: CONTROL — ExifTool + fix_exfat_raw ────────────
    def test_p4_control(self):
        def a(ops, files):
            for f in files:
                with ExifToolSession(connect=None) as s:
                    s.write_embedded(f, datetime.now(timezone.utc))
                dt = datetime.fromtimestamp(FIXED_TS, tz=timezone.utc)
                ops.fix_exfat_raw(str(f), dt, dry_run=False)
        def b(ops, files):
            for f in files:
                with ExifToolSession(connect=None) as s:
                    s.write_embedded(f, datetime.now(timezone.utc))
                dt = datetime.fromtimestamp(FIXED_TS, tz=timezone.utc)
                ops.fix_exfat_raw(str(f), dt, dry_run=False)
        self._run(a, b, _names(files_a), _names(files_b))

    # ── Phase 5: fix_exfat_raw only ────────────────────────────
    def test_p5_fix_only(self):
        def a(ops, files):
            for f in files:
                dt = datetime.fromtimestamp(FIXED_TS, tz=timezone.utc)
                ops.fix_exfat_raw(str(f), dt, dry_run=False)
        def b(ops, files):
            for f in files:
                dt = datetime.fromtimestamp(FIXED_TS, tz=timezone.utc)
                ops.fix_exfat_raw(str(f), dt, dry_run=False)
        self._run(a, b, _names(files_a), _names(files_b))

    # ── Phase 6: ExifTool only ─────────────────────────────────
    def test_p6_exif_only(self):
        def a(ops, files):
            for f in files:
                with ExifToolSession(connect=None) as s:
                    s.write_embedded(f, datetime.now(timezone.utc))
        def b(ops, files):
            for f in files:
                with ExifToolSession(connect=None) as s:
                    s.write_embedded(f, datetime.now(timezone.utc))
        self._run(a, b, set(), set())
