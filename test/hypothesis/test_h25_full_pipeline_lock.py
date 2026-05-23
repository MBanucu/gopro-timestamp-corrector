"""H25: Verify server serialization prevents parallel corruption during full pipeline.

Two mounts, each running full pipeline (exif batch + fix_exfat_raw),
all requests routed through the shared server — no corruption expected.

Each mount uses a distinct timestamp so cross-mount corruption is detectable.
"""
from exiftool_session import ExifToolSession
from strategies.exfat_raw import ExfatRawIO, ExfatRawFilesystem, ExfatRawOps
from test.shared import decompress_sparse_image, prepare_sparse_image, \
    setup_loop_device, teardown_loop_device
from datetime import datetime, timezone
import os, threading, unittest
from pathlib import Path

_BD = str(Path(__file__).resolve().parent.parent.parent / 'src')
import sys
if _BD not in sys.path: sys.path.insert(0, _BD)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / 'test'))


# Distinct timestamps per mount so cross-mount corruption is detectable.
TS_A = 1712345600
TS_B = 1712345678


def _mount(test_case, label):
    gz = Path(__file__).parent.parent / 'sdcard.img.gz'
    if not gz.exists(): raise unittest.SkipTest('sdcard.img.gz not found')
    cached = Path(__file__).parent.parent / 'sdcard.img'
    decompress_sparse_image(gz, cached)
    work, img = prepare_sparse_image(gz, prefix=f'h25_{label}_')
    loop, mnt = setup_loop_device(str(img))
    test_case.addCleanup(teardown_loop_device, loop, mnt)
    import shutil
    test_case.addCleanup(shutil.rmtree, work, ignore_errors=True)
    target = Path(mnt) / 'DCIM' / '100GOPRO'
    files = sorted(target.glob('*'))
    io = ExfatRawIO(); fs = ExfatRawFilesystem(io)
    ops = ExfatRawOps(io, fs)
    return ops, files, loop, mnt, work


class H25_FullPipelineLock(unittest.TestCase):

    def test_server_serializes_full_pipeline(self):
        """Server serializes batch write + fix_exfat_raw — no corruption."""
        a = _mount(self, 'A'); b = _mount(self, 'B')
        ops_a, files_a, loop_a, mnt_a, work_a = a
        ops_b, files_b, loop_b, mnt_b, work_b = b

        failures = []

        def full_pipeline(ops, files, label, ts):
            """Run full pipeline through the shared server."""
            try:
                with ExifToolSession() as s:
                    pairs = [(f, datetime.now(timezone.utc)) for f in files]
                    s.write_embedded_batch(pairs)

                for f in files:
                    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                    ops.fix_exfat_raw(str(f), dt, dry_run=False)
            except Exception as e:
                failures.append(f'{label} exception: {e}')

        threads = [
            threading.Thread(target=full_pipeline,
                             args=(ops_a, files_a, 'A', TS_A)),
            threading.Thread(target=full_pipeline,
                             args=(ops_b, files_b, 'B', TS_B)),
        ]
        for t in threads: t.start()
        for t in threads: t.join()

        # Verify: each mount's files must have the expected timestamp.
        # Any deviation means cross-mount corruption.
        for f in files_a:
            raw = ops_a.read_mtime_raw(str(f))
            if raw != TS_A:
                failures.append(
                    f'A {f.name}: expected {TS_A}, got {raw}')
        for f in files_b:
            raw = ops_b.read_mtime_raw(str(f))
            if raw != TS_B:
                failures.append(
                    f'B {f.name}: expected {TS_B}, got {raw}')

        if failures:
            self.fail(f'{len(failures)} corruptions:\n' + '\n'.join(failures[:10]))
