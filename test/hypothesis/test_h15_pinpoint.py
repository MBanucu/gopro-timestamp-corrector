"""H15: Pinpoint which operation fails under high parallel load.

H14 confirmed that concurrent operations across 2 mounts cause
directory entry corruption.  The corrected mtime is overwritten
by today's timestamp (from the exif metadata write).

This test isolates THREE phases to find exactly when the overwrite occurs:

Phase A: SINGLE exif write on ONE file, then fix+verify ALL files.
Phase B: exif write ALL files (sequential), then fix+verify ALL (parallel).
Phase C: exif write ALL files (parallel), fix ALL (parallel), verify ALL (parallel).

Each phase tells us whether the corruption happens:
- During exif writes (B vs C) — triggered by concurrent exif operations
- During fix_exfat_raw (B vs A) — triggered by writeback during raw-block write
- During verification (each phase's verify step) — triggered by read
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
    work, img = prepare_sparse_image(gz, prefix=f'h15_{label}_')
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


class H15_Pinpoint(unittest.TestCase):

    def _check_all(self, ops_list, files, label, phase):
        failures = []
        for i, (ops, f) in enumerate(zip(ops_list, files)):
            raw = ops.read_mtime_raw(str(f))
            if raw != FIXED_TS:
                failures.append(f'{label}[{i}] {f.name}: raw={raw} ({phase})')
        return failures

    def _fix_all(self, ops_list, files):
        for ops, f in zip(ops_list, files):
            dt = datetime.fromtimestamp(FIXED_TS, tz=timezone.utc)
            ops.fix_exfat_raw(str(f), dt, dry_run=False)

    # ── Phase A: sequential exif on ONE file, then parallel fix+verify ──
    def test_phase_A_one_exif_then_parallel(self):
        """Single exif write → parallel fix+verify on both mounts."""
        a = _setup('A'); b = _setup('B')
        try:
            ops_a, files_a, loop_a, mnt_a, work_a = a
            ops_b, files_b, loop_b, mnt_b, work_b = b

            # Exif write on just ONE file (mount A only)
            with ExifToolSession() as session:
                session.write_embedded(files_a[0], datetime.now(timezone.utc))

            # Parallel fix on ALL files on both mounts
            failures = []
            def fix_all(ops_list, files, label):
                for ops, f in zip(ops_list, files):
                    dt = datetime.fromtimestamp(FIXED_TS, tz=timezone.utc)
                    ops.fix_exfat_raw(str(f), dt, dry_run=False)
                f2 = self._check_all(ops_list, files, label, 'phase_A')
                failures.extend(f2)

            threads = [
                threading.Thread(target=fix_all, args=(ops_a, files_a, 'A')),
                threading.Thread(target=fix_all, args=(ops_b, files_b, 'B')),
            ]
            for t in threads: t.start()
            for t in threads: t.join()

            if failures:
                self.fail(f'{len(failures)} failures:\n' + '\n'.join(failures[:10]))
        finally:
            _teardown(*a[2:4], a[4]); _teardown(*b[2:4], b[4])

    # ── Phase B: sequential exif on ALL files, then parallel fix+verify ──
    def test_phase_B_all_exif_sequential(self):
        """Sequential exif writes (all files) → parallel fix+verify."""
        a = _setup('A'); b = _setup('B')
        try:
            ops_a, files_a, loop_a, mnt_a, work_a = a
            ops_b, files_b, loop_b, mnt_b, work_b = b

            # Exif writes sequential on each mount (but mounts in parallel)
            def exif_all(files, label):
                with ExifToolSession() as session:
                    for f in files:
                        session.write_embedded(f, datetime.now(timezone.utc))

            threads = [
                threading.Thread(target=exif_all, args=(files_a, 'A')),
                threading.Thread(target=exif_all, args=(files_b, 'B')),
            ]
            for t in threads: t.start()
            for t in threads: t.join()

            # Parallel fix+verify
            failures = []
            def fix_then_check(ops_list, files, label):
                for ops, f in zip(ops_list, files):
                    dt = datetime.fromtimestamp(FIXED_TS, tz=timezone.utc)
                    ops.fix_exfat_raw(str(f), dt, dry_run=False)
                f2 = self._check_all(ops_list, files, label, 'phase_B')
                failures.extend(f2)

            threads = [
                threading.Thread(target=fix_then_check, args=(ops_a, files_a, 'A')),
                threading.Thread(target=fix_then_check, args=(ops_b, files_b, 'B')),
            ]
            for t in threads: t.start()
            for t in threads: t.join()

            if failures:
                self.fail(f'{len(failures)} failures:\n' + '\n'.join(failures[:10]))
        finally:
            _teardown(*a[2:4], a[4]); _teardown(*b[2:4], b[4])

    # ── Phase C: everything parallel ──
    def test_phase_C_all_parallel(self):
        """Fully parallel: exif+fix+verify interleaved (original H14)."""
        a = _setup('A'); b = _setup('B')
        try:
            ops_a, files_a, loop_a, mnt_a, work_a = a
            ops_b, files_b, loop_b, mnt_b, work_b = b

            failures = []
            def full_pipeline(ops_list, files, label):
                for i, (ops, f) in enumerate(zip(ops_list, files)):
                    with ExifToolSession() as session:
                        session.write_embedded(f, datetime.now(timezone.utc))
                    dt = datetime.fromtimestamp(FIXED_TS, tz=timezone.utc)
                    ops.fix_exfat_raw(str(f), dt, dry_run=False)
                    raw = ops.read_mtime_raw(str(f))
                    if raw != FIXED_TS:
                        failures.append(
                            f'{label}[{i}] {f.name}: raw={raw} (phase_C)')

            threads = [
                threading.Thread(target=full_pipeline,
                                 args=(ops_a, files_a, 'A')),
                threading.Thread(target=full_pipeline,
                                 args=(ops_b, files_b, 'B')),
            ]
            for t in threads: t.start()
            for t in threads: t.join()

            # Final verification
            failures.extend(self._check_all(ops_a, files_a, 'A', 'phase_C_final'))
            failures.extend(self._check_all(ops_b, files_b, 'B', 'phase_C_final'))

            if failures:
                self.fail(f'{len(failures)} failures:\n' + '\n'.join(failures[:20]))
        finally:
            _teardown(*a[2:4], a[4]); _teardown(*b[2:4], b[4])

    # ── Phase D: exif parallel → fix sequential → verify ──
    def test_phase_D_exif_parallel_fix_sequential(self):
        """Exif writes in parallel → fix+verify SEQUENTIAL (one mount at a time)."""
        a = _setup('A'); b = _setup('B')
        try:
            ops_a, files_a, loop_a, mnt_a, work_a = a
            ops_b, files_b, loop_b, mnt_b, work_b = b

            # Exif writes parallel
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

            # Fix+verify SEQUENTIAL (mount A, then mount B)
            failures = []
            for label, ops_list, files in [('A', ops_a, files_a), ('B', ops_b, files_b)]:
                for ops, f in zip(ops_list, files):
                    dt = datetime.fromtimestamp(FIXED_TS, tz=timezone.utc)
                    ops.fix_exfat_raw(str(f), dt, dry_run=False)
                f2 = self._check_all(ops_list, files, label, 'phase_D')
                failures.extend(f2)

            if failures:
                self.fail(f'{len(failures)} failures:\n' + '\n'.join(failures[:10]))
        finally:
            _teardown(*a[2:4], a[4]); _teardown(*b[2:4], b[4])
