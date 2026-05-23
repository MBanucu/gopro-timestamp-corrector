"""H25: Verify holding exif lock for ENTIRE write_all prevents parallel corruption.

Current pattern: exif lock covers batch write only, fix_exfat_raw runs unlocked.
Hypothesis: holding the lock for BOTH batch write AND fix_exfat_raw prevents
the kernel exFAT driver cross-mount DE corruption under parallel load.

Test: two mounts, each running full pipeline (exif batch + fix_exfat_raw),
with the lock held for the entire write_all duration — no corruption expected.
"""
from exiftool_session import ExifToolSession, EXIFTOOL_WRITE_LOCK
from strategies.exfat_raw import ExfatRawIO, ExfatRawFilesystem, ExfatRawOps
from test.shared import decompress_sparse_image, prepare_sparse_image, \
    setup_loop_device, teardown_loop_device
from datetime import datetime, timezone
import fcntl, os, threading, unittest
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

    def test_hold_lock_for_entire_write_all(self):
        """exif lock held for batch + fix_exfat_raw — no corruption expected."""
        a = _mount('A'); b = _mount('B')
        ops_a, rec_a, files_a, loop_a, mnt_a, work_a = a
        ops_b, rec_b, files_b, loop_b, mnt_b, work_b = b

        failures = []

        def full_pipeline(ops, rec, files, label):
            """Simulate Writer.write_all with lock held for entire duration."""
            with open(EXIFTOOL_WRITE_LOCK, 'w') as lk:
                fcntl.flock(lk, fcntl.LOCK_EX)
                try:
                    # Batch write
                    with ExifToolSession() as s:
                        pairs = [(f, datetime.now(timezone.utc)) for f in files]
                        s.write_embedded_batch(pairs)

                    # fix_exfat_raw on each file (still under the lock)
                    for f in files:
                        ts = 1712345678
                        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                        ops.fix_exfat_raw(str(f), dt, dry_run=False)
                except Exception as e:
                    failures.append(f'{label} exception: {e}')

                # Verify (under the lock)
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
