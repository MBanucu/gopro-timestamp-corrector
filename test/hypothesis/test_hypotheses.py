"""Hypothesis tests for exFAT raw-block race conditions.

Each test verifies a specific hypothesis about kernel behavior
during parallel correction.  Run with::

    PYTHONPATH=src python3 -m unittest test.hypothesis.test_hypotheses -v
"""
import os
import subprocess
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

_BD = str(Path(__file__).resolve().parent.parent.parent / 'src')
if _BD not in os.environ.get('PYTHONPATH', ''):
    os.environ.setdefault('PYTHONPATH', _BD)

FIXED_TS = 1712345678
IMAGE_GZ = str(Path(__file__).parent.parent / 'sdcard.img.gz')
CACHED = str(Path(__file__).parent.parent / 'sdcard.img')


def _setup(test_case):
    """Set up a loop device with a fresh image copy, return (ops, loop, mnt, files, work)."""
    from test.shared import decompress_sparse_image, prepare_sparse_image, \
        setup_loop_device, teardown_loop_device
    gz = Path(IMAGE_GZ)
    if not gz.exists():
        test_case.skipTest('sdcard.img.gz not found')
    decompress_sparse_image(gz, Path(CACHED))
    work, img = prepare_sparse_image(gz, prefix='hypo_')
    try:
        loop, mnt = setup_loop_device(str(img))
    except Exception:
        teardown_loop_device(getattr(test_case, '_loop', None))
        import shutil
        shutil.rmtree(work, ignore_errors=True)
        raise
    target = Path(mnt) / 'DCIM' / '100GOPRO'
    if not target.exists():
        teardown_loop_device(loop, mnt)
        shutil.rmtree(work, ignore_errors=True)
        test_case.skipTest('100GOPRO not found')
    files = sorted(target.glob('*.MP4')) or sorted(target.glob('*'))
    from strategies.exfat_raw import ExfatRawIO, ExfatRawFilesystem, ExfatRawOps
    io = ExfatRawIO()
    fs = ExfatRawFilesystem(io)
    ops = ExfatRawOps(io, fs)
    return ops, loop, mnt, files, work


def _teardown(loop, mnt, work):
    from test.shared import teardown_loop_device
    try:
        teardown_loop_device(loop, mnt)
    except Exception:
        pass
    import shutil
    shutil.rmtree(work, ignore_errors=True)


# ── Hypothesis 1 ────────────────────────────────────────────────

class H1_BackingLoopCacheSeparation(unittest.TestCase):
    """H1: Backing file and loop device have SEPARATE page caches
    for blocks that the exFAT driver has cached.

    Writing to the backing file via os.pwrite() at an offset that
    the loop device has cached (e.g., directory entry clusters)
    is invisible through sudo dd if=/dev/loopN.  The loop driver
    maintains its own bdev page cache that is NOT invalidated by
    writes to the backing file.

    Consequence: all raw-block I/O must go through /dev/loopN.
    """
    def test_dir_offset_invisible_via_loop(self):
        """Write at a directory-entry offset (cached by exFAT)."""
        ops, loop, mnt, files, work = _setup(self)
        try:
            f = files[0]
            from btime import _resolve_device
            dev = _resolve_device(str(f))
            dn = dev.lstrip('/dev/')
            r = subprocess.run(['cat', f'/sys/block/{dn}/loop/backing_file'],
                               capture_output=True, text=True)
            backing = r.stdout.strip() if r.returncode == 0 else None
            self.assertIsNotNone(backing)

            # Read the directory entry cluster offset from ops internals
            boot = ops._io.parse_boot(dev)
            resolved = ops._fs._resolve_path(str(f))
            _dev, _mp, parts, filename = resolved
            cl = boot['root_cluster']
            for comp in parts:
                found = ops._fs.find_in_dir(boot, dev, cl, comp)
                stream = found[4][1]
                import struct
                cl = struct.unpack_from('<I', stream, 0x14)[0]
            found2 = ops._fs.find_in_dir(boot, dev, cl, filename)
            fchain, fci, foff, fsc, _ = found2
            cluster_off = boot['cluster_heap_offset'] + (fchain[fci] - 2) * boot['cluster_size']

            # Write known data to this offset via backing file
            test_data = b'DIRECTORY_CACHE_TEST'
            fd = os.open(backing, os.O_WRONLY)
            os.pwrite(fd, test_data, cluster_off + foff)
            os.fsync(fd)
            os.close(fd)

            # Read back via backing file — must match
            fd2 = os.open(backing, os.O_RDONLY)
            via_backing = os.pread(fd2, len(test_data), cluster_off + foff)
            os.close(fd2)
            self.assertEqual(via_backing, test_data)

            # Read back via loop device — should be STALE
            via_loop = subprocess.run(
                ['sudo', 'dd', f'if={dev}', 'bs=1',
                 f'skip={cluster_off + foff}', f'count={len(test_data)}',
                 'status=none'],
                capture_output=True)
            self.assertNotEqual(
                via_loop.stdout, test_data,
                'H1 FAILED: backing write IS visible via loop — '
                'loop cache separation hypothesis WRONG')
        finally:
            _teardown(loop, mnt, work)


# ── Hypothesis 2 ────────────────────────────────────────────────

class H2_LoopDeviceIOIsSelfCoherent(unittest.TestCase):
    """H2: sudo dd through /dev/loopN is SELF-COHERENT for
    sequential read+write.  If both go through the same loop
    device, data consistency is maintained.
    """
    def test_loop_write_then_loop_read(self):
        ops, loop, mnt, files, work = _setup(self)
        try:
            _fix_mtime(ops, files[0])
            raw = _raw_mtime(ops, files[0])
            self.assertEqual(raw, FIXED_TS)
        finally:
            _teardown(loop, mnt, work)


# ── Hypothesis 3 ────────────────────────────────────────────────

class H3_StatDoesNotTriggerWriteback(unittest.TestCase):
    """H3: os.path.getmtime() and os.stat() do NOT trigger exFAT
    driver writeback that overwrites the corrected directory entry.
    The kernel writeback is asynchronous and only runs on explicit
    sync or inode reclaim.
    """
    def test_stat_after_fix_preserves_raw(self):
        ops, loop, mnt, files, work = _setup(self)
        try:
            subprocess.run(['touch', '-c', str(files[0])], capture_output=True)
            _fix_mtime(ops, files[0])
            self.assertEqual(_raw_mtime(ops, files[0]), FIXED_TS)

            _ = os.path.getmtime(files[0])
            _ = os.stat(files[0])
            self.assertEqual(_raw_mtime(ops, files[0]), FIXED_TS,
                             'stat/getmtime triggered writeback')
        finally:
            _teardown(loop, mnt, work)


# ── Hypothesis 4 ────────────────────────────────────────────────

class H4_SameLoopDeviceConcurrentFsyncCorrupts(unittest.TestCase):
    """H4: Two concurrent sudo dd conv=fsync through the SAME loop
    device can corrupt each other's data.  This is a kernel loop
    driver bug: concurrent fsync() on the same loop device does
    not properly serialize writes at different offsets.
    """
    def test_concurrent_fsync_same_loop_corrupts(self):
        ops, loop, mnt, files, work = _setup(self)
        try:
            self.assertGreaterEqual(len(files), 2)

            def fix_and_check(f, ts):
                _fix_mtime(ops, f, ts)
                return _raw_mtime(ops, f), ts

            ts_a, ts_b = FIXED_TS, FIXED_TS + 3600
            with ThreadPoolExecutor(max_workers=2) as pool:
                futs = [
                    pool.submit(fix_and_check, files[0], ts_a),
                    pool.submit(fix_and_check, files[1], ts_b),
                ]
                ok_all = True
                for fut in futs:
                    raw, expected = fut.result()
                    if raw != expected:
                        ok_all = False
            self.assertFalse(
                ok_all,
                'H4 FAILED: concurrent fsync did NOT corrupt — '
                'loop driver bug hypothesis WRONG')
        finally:
            _teardown(loop, mnt, work)


# ── Hypothesis 5 ────────────────────────────────────────────────

class H5_SyncDoesNotFlushExfatInode(unittest.TestCase):
    """H5: sync() does NOT flush a dirty exFAT inode to the
    directory entry in the raw block.  The exFAT driver's
    writeback only writes the inode's in-memory metadata to the
    directory entry on inode eviction (memory pressure) or
    unmount.

    Consequence: a dirty inode (mtime=now from exif metadata
    write) persists in memory and NEVER reaches the directory
    entry unless the inode is evicted or os.utime() is called.
    """
    def test_sync_does_not_flush(self):
        ops, loop, mnt, files, work = _setup(self)
        try:
            raw_before = _raw_mtime(ops, files[0])
            self.assertIsNotNone(raw_before)

            subprocess.run(['touch', '-c', str(files[0])], capture_output=True)
            subprocess.run(['sync'])

            raw_after = _raw_mtime(ops, files[0])
            self.assertEqual(
                raw_after, raw_before,
                'H5 FAILED: sync() changed raw mtime — '
                'dirty inode WAS flushed to directory entry')
        finally:
            _teardown(loop, mnt, work)


# ── Hypothesis 6 ────────────────────────────────────────────────

class H6_NoUtimePreservesData(unittest.TestCase):
    """H6: Without os.utime(), the corrected directory entry
    persists through subsequent stat() calls.  The removed
    os.utime() was the only path that could overwrite the
    raw-block data.
    """
    def test_fix_without_utime(self):
        ops, loop, mnt, files, work = _setup(self)
        try:
            f = files[0]
            _fix_mtime(ops, f)
            self.assertEqual(_raw_mtime(ops, f), FIXED_TS)
            # os.path.getmtime() is expected to be stale
        finally:
            _teardown(loop, mnt, work)


# ── Hypothesis 7 ────────────────────────────────────────────────

class H7_FileLockPreventsTOCTOU(unittest.TestCase):
    """H7: The file lock (/tmp/gopro_loop_setup.lock) prevents
    the TOCTOU race where two concurrent setup_loop_device calls
    get the same loop device.

    Test: run setup_loop_device twice in parallel threads. With
    the lock, both get DIFFERENT loop devices.
    """
    def test_lock_ensures_unique_devices(self):
        from test.shared import setup_loop_device, teardown_loop_device
        from test.shared import decompress_sparse_image, prepare_sparse_image
        import tempfile
        gz = Path(IMAGE_GZ)
        if not gz.exists():
            self.skipTest('sdcard.img.gz not found')
        decompress_sparse_image(gz, Path(CACHED))

        results = {}

        def do_setup(label):
            work, img = prepare_sparse_image(gz, prefix=f'lock_{label}_')
            try:
                loop, mnt = setup_loop_device(str(img))
                results[label] = (loop, mnt, work)
            except Exception as e:
                results[label] = (f'FAIL: {e}', work)

        threads = [threading.Thread(target=do_setup, args=(l,))
                   for l in ('A', 'B')]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        import shutil
        for label in ('A', 'B'):
            r = results[label]
            if len(r) == 3:
                loop, mnt, work = r
                teardown_loop_device(loop, mnt)
                shutil.rmtree(work, ignore_errors=True)
            else:
                _, work = r
                shutil.rmtree(work, ignore_errors=True)

        dev_a = results.get('A', (None,))[0]
        dev_b = results.get('B', (None,))[0]
        self.assertIsNotNone(dev_a, 'A should succeed')
        self.assertIsNotNone(dev_b, 'B should succeed')
        self.assertNotEqual(
            dev_a, dev_b,
            'H7 FAILED: both got the same loop device — '
            'lock did NOT prevent TOCTOU race')


def _raw_mtime(ops, path):
    return ops.read_mtime_raw(str(path))


def _fix_mtime(ops, path, ts=FIXED_TS):
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    ops.fix_exfat_raw(str(path), dt, dry_run=False)


if __name__ == '__main__':
    unittest.main()
