"""H25: Verify server serialization prevents parallel corruption during full pipeline.

Two mounts, each running full pipeline (exif batch + fix_exfat_raw),
all requests routed through the shared server — no corruption expected.
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


def _mount(label):
    gz = Path(__file__).parent.parent / 'sdcard.img.gz'
    if not gz.exists(): raise unittest.SkipTest('sdcard.img.gz not found')
    cached = Path(__file__).parent.parent / 'sdcard.img'
    decompress_sparse_image(gz, cached)
    work, img = prepare_sparse_image(gz, prefix=f'h25_{label}_')
    loop, mnt = setup_loop_device(str(img))
    target = Path(mnt) / 'DCIM' / '100GOPRO'
    files = sorted(target.glob('*'))
    io = ExfatRawIO(); fs = ExfatRawFilesystem(io)
    ops = ExfatRawOps(io, fs)
    rec = {f.name: ops.read_mtime_raw(str(f)) for f in files}
    return ops, rec, files, loop, mnt, work


def _umount(loop, mnt, work):
    try: teardown_loop_device(loop, mnt)
    except: pass
    import shutil; shutil.rmtree(work, ignore_errors=True)


class H25_FullPipelineLock(unittest.TestCase):

    def test_server_serializes_full_pipeline(self):
        """Server serializes batch write + fix_exfat_raw — no corruption."""
        a = _mount('A'); b = _mount('B')
        ops_a, rec_a, files_a, loop_a, mnt_a, work_a = a
        ops_b, rec_b, files_b, loop_b, mnt_b, work_b = b

        failures = []

        def full_pipeline(ops, rec, files, label):
            """Run full pipeline through the shared server."""
            try:
                # Batch write via server (serialized automatically)
                with ExifToolSession() as s:
                    pairs = [(f, datetime.now(timezone.utc)) for f in files]
                    s.write_embedded_batch(pairs)

                # fix_exfat_raw on each file
                for f in files:
                    ts = 1712345678
                    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                    ops.fix_exfat_raw(str(f), dt, dry_run=False)
            except Exception as e:
                failures.append(f'{label} exception: {e}')

            # Verify
            for f in files:
                raw = ops.read_mtime_raw(str(f))
                if raw != rec.get(f.name):
                    failures.append(
                        f'{label} {f.name}: {rec.get(f.name)} -> {raw}')

        threads = [threading.Thread(target=full_pipeline,
                                    args=(ops_a, rec_a, files_a, 'A')),
                   threading.Thread(target=full_pipeline,
                                    args=(ops_b, rec_b, files_b, 'B'))]
        for t in threads: t.start()
        for t in threads: t.join()

        _umount(loop_a, mnt_a, work_a); _umount(loop_b, mnt_b, work_b)

        if failures:
            self.fail(f'{len(failures)} corruptions:\n' + '\n'.join(failures[:10]))
