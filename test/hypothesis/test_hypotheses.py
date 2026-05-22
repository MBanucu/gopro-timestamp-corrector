"""Hypothesis tests for exFAT raw-block race conditions.

Each hypothesis is tested in a single test method with a clear
expected outcome.  Run with::

    PYTHONPATH=src python3 -m unittest test.hypothesis -v

"""
import os
import struct
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
GZ_PATH = Path(__file__).parent.parent / 'sdcard.img.gz'


# ── shared loop-device lifecycle ────────────────────────────────

class LoopDeviceTest(unittest.TestCase):
    """Base for tests needing a loop device with a fresh image copy."""

    @classmethod
    def setUpClass(cls):
        from test.shared import decompress_sparse_image, prepare_sparse_image, \
            setup_loop_device, teardown_loop_device
        if not GZ_PATH.exists():
            raise unittest.SkipTest('sdcard.img.gz not found')
        cached = Path(__file__).parent.parent / 'sdcard.img'
        decompress_sparse_image(GZ_PATH, cached)
        cls._work, cls._img = prepare_sparse_image(GZ_PATH, prefix='hypo_')
        try:
            cls._loop, cls._mnt = setup_loop_device(str(cls._img))
        except Exception:
            teardown_loop_device(getattr(cls, '_loop', None))
            import shutil
            shutil.rmtree(cls._work, ignore_errors=True)
            raise
        cls._target = Path(cls._mnt) / 'DCIM' / '100GOPRO'
        if not cls._target.exists():
            teardown_loop_device(cls._loop, cls._mnt)
            shutil.rmtree(cls._work, ignore_errors=True)
            raise unittest.SkipTest('100GOPRO not found')
        cls._files = sorted(cls._target.glob('*.MP4')) or sorted(cls._target.glob('*'))
        from strategies.exfat_raw import ExfatRawIO, ExfatRawFilesystem, ExfatRawOps
        cls._io = ExfatRawIO()
        cls._ops = ExfatRawOps(cls._io, ExfatRawFilesystem(cls._io))

    @classmethod
    def tearDownClass(cls):
        from test.shared import teardown_loop_device
        teardown_loop_device(cls._loop, cls._mnt)
        import shutil
        shutil.rmtree(cls._work, ignore_errors=True)

    def raw_mtime(self, f=None):
        return self._ops.read_mtime_raw(str(f or self._files[0]))

    def fix_mtime(self, ts=FIXED_TS, f=None):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        self._ops.fix_exfat_raw(str(f or self._files[0]), dt, dry_run=False)


# ── Hypothesis tests ────────────────────────────────────────────

class H1_CacheSeparation(LoopDeviceTest):
    """H1: Backing file and loop device have SEPARATE page caches.

    For offsets that the exFAT driver has already cached (e.g.
    directory entry clusters), writing to the backing file via
    os.pwrite is invisible through the loop device.

    Consequence: all raw I/O must go through /dev/loopN.
    """
    def test_write_to_backing_is_invisible_via_loop(self):
        f = self._files[0]
        from btime import _resolve_device
        dev = _resolve_device(str(f))
        boot = self._io.parse_boot(dev)
        resolved = self._ops._fs._resolve_path(str(f))
        _, _, parts, filename = resolved
        cl = boot['root_cluster']
        for comp in parts:
            found = self._ops._fs.find_in_dir(boot, dev, cl, comp)
            stream = found[4][1]
            cl = struct.unpack_from('<I', stream, 0x14)[0]
        found2 = self._ops._fs.find_in_dir(boot, dev, cl, filename)
        fchain, fci, foff, _, _ = found2
        cluster_off = boot['cluster_heap_offset'] + (fchain[fci] - 2) * boot['cluster_size']
        entry_off = cluster_off + foff

        dn = dev.lstrip('/dev/')
        r = subprocess.run(['cat', f'/sys/block/{dn}/loop/backing_file'],
                           capture_output=True, text=True)
        backing = r.stdout.strip() if r.returncode == 0 else None
        self.assertIsNotNone(backing)

        test_data = b'CACHE_SEPARATION_TEST'
        fd = os.open(backing, os.O_WRONLY)
        os.pwrite(fd, test_data, entry_off)
        os.fsync(fd)
        os.close(fd)

        via_backing = os.pread(os.open(backing, os.O_RDONLY), len(test_data), entry_off)
        self.assertEqual(via_backing, test_data)

        via_loop = subprocess.run(
            ['sudo', 'dd', f'if={dev}', 'bs=1',
             f'skip={entry_off}', f'count={len(test_data)}', 'status=none'],
            capture_output=True)
        self.assertNotEqual(via_loop.stdout, test_data,
                            'H1 FAILED: backing write visible via loop')


class H2_SequentialCoherence(LoopDeviceTest):
    """H2: sudo dd through /dev/loopN is self-coherent for
    sequential writes followed by reads (same I/O path)."""
    def test_write_then_read(self):
        self.fix_mtime()
        self.assertEqual(self.raw_mtime(), FIXED_TS)


class H3_StatIsSafe(LoopDeviceTest):
    """H3: os.path.getmtime and os.stat do NOT trigger exFAT
    writeback that overwrites the corrected directory entry."""
    def test_stat_after_fix_preserves_data(self):
        subprocess.run(['touch', '-c', str(self._files[0])], capture_output=True)
        self.fix_mtime()
        self.assertEqual(self.raw_mtime(), FIXED_TS)
        os.path.getmtime(self._files[0])
        os.stat(self._files[0])
        self.assertEqual(self.raw_mtime(), FIXED_TS)


class H4_ConcurrentFsyncCorrupts(LoopDeviceTest):
    """H4: Two concurrent sudo dd conv=fsync through the SAME
    loop device (different offsets) can corrupt each other's data.
    This is a kernel loop driver bug."""
    def test_two_threads_same_loop_corrupt(self):
        self.assertGreaterEqual(len(self._files), 2)
        def fix_check(f, ts):
            self.fix_mtime(ts, f)
            return self.raw_mtime(f), ts
        ts_a, ts_b = FIXED_TS, FIXED_TS + 3600
        with ThreadPoolExecutor(max_workers=2) as pool:
            futs = [pool.submit(fix_check, self._files[0], ts_a),
                    pool.submit(fix_check, self._files[1], ts_b)]
            all_ok = all(raw == exp for fut in futs for raw, exp in [fut.result()])
        self.assertFalse(all_ok, 'H4 FAILED: concurrent fsync did NOT corrupt')


class H5_SyncDoesNotFlush(LoopDeviceTest):
    """H5: sync() does NOT flush a dirty exFAT inode to the
    directory entry.  Inode writeback only happens on eviction
    or explicit unmount."""
    def test_sync_does_not_change_raw_block(self):
        raw_before = self.raw_mtime()
        subprocess.run(['touch', '-c', str(self._files[0])], capture_output=True)
        subprocess.run(['sync'])
        self.assertEqual(self.raw_mtime(), raw_before,
                         'H5 FAILED: sync flushed inode to raw block')


class H6_NoUtimeIsSafe(LoopDeviceTest):
    """H6: Without os.utime, the corrected directory entry
    persists.  The removed os.utime was the only path that
    could overwrite raw-block data via the exFAT driver."""
    def test_fix_without_utime(self):
        self.fix_mtime()
        self.assertEqual(self.raw_mtime(), FIXED_TS)


class H7_FileLockPreventsTOCTOU(unittest.TestCase):
    """H7: The /tmp/gopro_loop_setup.lock ensures two concurrent
    setup_loop_device calls get different loop devices."""
    def test_parallel_setup_gets_unique_devices(self):
        from test.shared import decompress_sparse_image, prepare_sparse_image, \
            setup_loop_device, teardown_loop_device
        import tempfile
        if not GZ_PATH.exists():
            self.skipTest('sdcard.img.gz not found')
        cached = Path(__file__).parent.parent / 'sdcard.img'
        decompress_sparse_image(GZ_PATH, cached)
        results = {}
        def do_setup(label):
            work, img = prepare_sparse_image(GZ_PATH, prefix=f'lock_{label}_')
            try:
                loop, mnt = setup_loop_device(str(img))
                results[label] = (loop, mnt, work)
            except Exception as e:
                results[label] = (f'FAIL: {e}', work)
        threads = [threading.Thread(target=do_setup, args=(l,))
                   for l in ('A', 'B')]
        for t in threads: t.start()
        for t in threads: t.join()
        import shutil
        for label in ('A', 'B'):
            r = results[label]
            if len(r) == 3:
                teardown_loop_device(r[0], r[1])
                shutil.rmtree(r[2], ignore_errors=True)
            else:
                shutil.rmtree(r[1], ignore_errors=True)
        self.assertEqual(len(results), 2)
        self.assertNotEqual(results['A'][0], results['B'][0],
                            'H7 FAILED: both got same loop device')
