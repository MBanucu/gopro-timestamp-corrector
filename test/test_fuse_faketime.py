"""Integration test for the FUSE + faketime btime strategy on exFAT.

Tests that creating files under a FUSE exFAT mount wrapped with faketime
produces the correct birth time on disk — verified after remounting
via the kernel exFAT driver.
"""

import ctypes
import ctypes.util
import gzip
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


# ── statx helpers (ctypes, since os.statx is not available on all Pythons) ──

class statx_timestamp(ctypes.Structure):
    _fields_ = [
        ('tv_sec', ctypes.c_int64),
        ('tv_nsec', ctypes.c_uint32),
        ('__reserved', ctypes.c_int32),
    ]


class statx_buf(ctypes.Structure):
    _fields_ = [
        ('stx_mask', ctypes.c_uint32),
        ('stx_blksize', ctypes.c_uint32),
        ('stx_attributes', ctypes.c_uint64),
        ('stx_nlink', ctypes.c_uint32),
        ('stx_uid', ctypes.c_uint32),
        ('stx_gid', ctypes.c_uint32),
        ('stx_mode', ctypes.c_uint16),
        ('__spare0', ctypes.c_uint16 * 1),
        ('stx_ino', ctypes.c_uint64),
        ('stx_size', ctypes.c_uint64),
        ('stx_blocks', ctypes.c_uint64),
        ('stx_attributes_mask', ctypes.c_uint64),
        ('stx_atime', statx_timestamp),
        ('stx_btime', statx_timestamp),
        ('stx_ctime', statx_timestamp),
        ('stx_mtime', statx_timestamp),
        ('stx_rdev_major', ctypes.c_uint32),
        ('stx_rdev_minor', ctypes.c_uint32),
        ('stx_dev_major', ctypes.c_uint32),
        ('stx_dev_minor', ctypes.c_uint32),
        ('__spare2', ctypes.c_uint64 * 14),
    ]


STATX_BTIME = 0x00000200
AT_FDCWD = -100

_libc = ctypes.CDLL(ctypes.util.find_library('c'), use_errno=True)
_libc.statx.restype = ctypes.c_int
_libc.statx.argtypes = [
    ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_uint,
    ctypes.POINTER(statx_buf),
]


def read_btime(path: str) -> int | None:
    """Read btime (birth time) via statx(). Returns Unix timestamp or None."""
    buf = statx_buf()
    ret = _libc.statx(AT_FDCWD, path.encode(), 0, STATX_BTIME, ctypes.byref(buf))
    if ret == 0 and (buf.stx_mask & STATX_BTIME):
        return buf.stx_btime.tv_sec
    return None


def read_mtime(path: str) -> int | None:
    """Read mtime via statx(). Returns Unix timestamp or None."""
    from stat import ST_MTIME
    try:
        return int(os.stat(path).st_mtime)
    except OSError:
        return None


# ── Helpers ─────────────────────────────────────────────────────────────────

def decompress_sparse(gz_path, dest_path):
    """Decompress sparse image; no-op if dest exists."""
    if dest_path.exists():
        return dest_path
    KNOWN_SIZE = 8531738624
    CHUNK = 1024 * 1024
    fd = os.open(dest_path, os.O_CREAT | os.O_WRONLY)
    os.ftruncate(fd, KNOWN_SIZE)
    os.close(fd)
    zero = b'\x00' * CHUNK
    offset = 0
    with gzip.open(gz_path, 'rb') as src, open(dest_path, 'rb+') as dst:
        while True:
            chunk = src.read(CHUNK)
            if not chunk:
                break
            if chunk != zero[:len(chunk)]:
                os.lseek(dst.fileno(), offset, os.SEEK_SET)
                dst.write(chunk)
            offset += len(chunk)
    return dest_path


def run(cmd, **kwargs):
    """subprocess.run with sensible defaults."""
    kwargs.setdefault('capture_output', True)
    kwargs.setdefault('text', True)
    kwargs.setdefault('timeout', 30)
    return subprocess.run(cmd, **kwargs)


def format_delta(delta: timedelta) -> str:
    """Format a timedelta into a faketime offset string.

    Uses raw seconds format because faketime's multi-component format
    ('-Xd -Yh -Zm') has a parsing bug with combined day/hour/minute offsets.

    Always includes an explicit sign (+/-) so that positive offsets
    are recognised as relative offsets, not absolute timestamps.
    """
    total_sec = int(delta.total_seconds())
    if total_sec >= 0:
        return f'+{total_sec}'
    return str(total_sec)


# ── Test ────────────────────────────────────────────────────────────────────

@unittest.skipIf(
    not shutil.which('faketime'),
    'faketime not available — install libfaketime')
@unittest.skipIf(
    not shutil.which('mount.exfat-fuse'),
    'mount.exfat-fuse not available — install exfat/exfat-fuse')
@unittest.skipIf(
    shutil.which('udisksctl') is None,
    'udisksctl not available')
class TestFuseFaketimeBtime(unittest.TestCase):

    DELTAS_TO_TEST = [
        timedelta(hours=-2),
        timedelta(days=-1),
        timedelta(days=-7, hours=-3, minutes=-30),
    ]

    # ── class-level setup / teardown ────────────────────────────────

    @classmethod
    def setUpClass(cls):
        gz_path = Path(__file__).parent / 'sdcard.img.gz'
        if not gz_path.exists():
            raise unittest.SkipTest(f'Compressed image not found at {gz_path}')

        cached = Path(__file__).parent / 'sdcard.img'
        decompress_sparse(gz_path, cached)

        cls._work_dir = Path(tempfile.mkdtemp(prefix='gopro_fuse_test_'))
        cls.img_path = cls._work_dir / 'sdcard.img'
        run(['cp', '--sparse=always', str(cached), str(cls.img_path)], check=True)

        from loop_device import setup_loop_device, teardown_loop_device
        from strategies.mount import MountError
        try:
            cls.loop_dev, cls.mount_point = setup_loop_device(str(cls.img_path))
        except MountError as e:
            raise unittest.SkipTest(str(e))
        cls.target = Path(cls.mount_point) / 'DCIM' / '100GOPRO'
        cls.target.mkdir(parents=True, exist_ok=True)
        cls.addClassCleanup(teardown_loop_device, cls.loop_dev, cls.mount_point)
        cls.addClassCleanup(shutil.rmtree, cls._work_dir, ignore_errors=True)

    # ── per-test helpers: FUSE mount cycle ──────────────────────────

    def _ensure_mounted(self):
        """Make sure the loop device is mounted at self.mount_point."""
        if not self.mount_point:
            self.skipTest('No mount point')
        mp = Path(self.mount_point)
        try:
            mp.mkdir(parents=True, exist_ok=True)
        except OSError:
            run(['sudo', 'umount', '-l', str(mp)])
            mp.mkdir(parents=True, exist_ok=True)
        try:
            mounted = os.path.ismount(str(mp))
        except OSError:
            run(['sudo', 'umount', '-l', str(mp)])
            mounted = False
        if not mounted:
            r = run(['sudo', 'mount', self.loop_dev, str(mp)])
            if r.returncode != 0:
                self.skipTest(f'Failed to restore mount: {r.stderr.strip()}')

    def _fuse_cycle(self, delta: timedelta, fn):
        """Run *fn(mount_path)* under FUSE+faketime with given *delta*.

        1. Unmount kernel exfat
        2. Remount via FUSE+faketime
        3. Call fn(mount_path)  — create files, etc.
        4. Unmount FUSE, kill faketime
        5. Remount kernel exfat at the same mount_point
        """
        if not self.loop_dev or not self.mount_point:
            self.skipTest('No loop device or mount point')

        self._ensure_mounted()
        mount_path = self.mount_point

        r = run(['sudo', 'umount', mount_path])
        if r.returncode != 0:
            r = run(['sudo', 'umount', '-l', mount_path])
        if r.returncode != 0:
            self.skipTest(f'Could not unmount: {r.stderr.strip()}')

        run(['sudo', 'mkdir', '-p', mount_path], check=True)

        offset = format_delta(delta)
        uid = os.getuid()
        gid = os.getgid()
        proc = subprocess.Popen(
            ['sudo', 'faketime', '-f', offset,
             'mount.exfat-fuse', self.loop_dev, mount_path,
             '-o', f'uid={uid}', '-o', f'gid={gid}',
             '-o', 'allow_other', '-o', 'nonempty', '-o', 'auto_unmount'],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

        for _ in range(5000):
            if proc.poll() is not None:
                err = proc.stderr.read().strip() if proc.stderr else ''
                run(['sudo', 'mount', self.loop_dev, mount_path])
                self.skipTest(f'FUSE mount failed (exit {proc.returncode}): {err}')
            if os.path.ismount(mount_path):
                break
            time.sleep(0.002)
        else:
            err = proc.stderr.read().strip() if proc.stderr else ''
            run(['sudo', 'mount', self.loop_dev, mount_path])
            self.skipTest(f'FUSE mount timed out: {err}')

        try:
            fn(mount_path)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            if proc.stderr:
                proc.stderr.close()
            run(['sudo', 'umount', '-f', mount_path])
            run(['sudo', 'mount', self.loop_dev, mount_path])
            self.mount_point = mount_path

    # ── tests ───────────────────────────────────────────────────────

    def _run_single_delta(self, delta):
        """Run one FUSE+faketime cycle with *delta*, create a file, verify btime."""
        name = f'btime_test_{abs(int(delta.total_seconds()))}s.txt'

        def create_file(mount):
            Path(os.path.join(mount, name)).write_text('test')

        self._fuse_cycle(delta, create_file)

        kernel_path = os.path.join(self.mount_point, name)
        btime = read_btime(kernel_path)
        run(['sudo', 'rm', '-f', kernel_path])

        self.assertIsNotNone(btime, f'{name}: btime should be readable')

        now = int(time.time())
        expected = now + int(delta.total_seconds())
        diff = abs(btime - expected)

        self.assertLessEqual(
            diff, 5,
            f'{name} delta={delta}: btime={btime} expected≈{expected} '
            f'diff={diff}s')

    def test_btime_matches_faketime_offset(self):
        """Files created under FUSE+faketime get the expected btime on disk."""
        for delta in self.DELTAS_TO_TEST:
            with self.subTest(delta=delta):
                self._run_single_delta(delta)

    def test_original_files_unaffected(self):
        """Pre-existing files keep their btime through a FUSE remount cycle."""
        existing = sorted(self.target.iterdir())
        if not existing:
            self.skipTest('No existing files to test against')

        ref_file = str(existing[0])
        ref_btime_before = read_btime(ref_file)
        self.assertIsNotNone(ref_btime_before,
                             'Reference file should have readable btime')

        def touch_nothing(mount):
            pass

        self._fuse_cycle(timedelta(days=-1), touch_nothing)

        ref_btime_after = read_btime(ref_file)
        self.assertIsNotNone(ref_btime_after)
        self.assertEqual(
            ref_btime_before, ref_btime_after,
            f'btime of {existing[0].name} changed from '
            f'{ref_btime_before} to {ref_btime_after} after FUSE cycle')

    def test_new_file_btime_equals_faketime(self):
        """A new file's btime equals the faketime offset (within tolerance)."""
        delta = timedelta(days=-7, hours=-3, minutes=-30)
        name = 'btime_single.txt'

        def create_file(mount):
            Path(os.path.join(mount, name)).write_text('data')

        self._fuse_cycle(delta, create_file)

        kernel_path = os.path.join(self.mount_point, name)
        btime = read_btime(kernel_path)
        run(['sudo', 'rm', '-f', kernel_path])

        self.assertIsNotNone(btime)
        now = int(time.time())
        expected = now + int(delta.total_seconds())
        diff = abs(btime - expected)
        self.assertLessEqual(
            diff, 5,
            f'btime={btime} expected≈{expected} diff={diff}s')

    def test_os_utime_does_not_change_existing_file_btime(self):
        """os.utime() on an existing file under FUSE+faketime does not
        change its btime — the btime was set once at file creation and
        utimensat only touches atime/mtime, not creation time.

        This confirms that for EXISTING GoPro files (the primary use
        case), the FUSE+faketime approach does NOT correct btime. Only
        new files created under the faked clock get the shifted btime.
        """
        name = 'utime_btime_test.txt'
        delta_create = timedelta(hours=-2)

        # ── 1. Create file under FUSE+faketime with delta_create ──────
        def create_file(mount):
            Path(os.path.join(mount, name)).write_text('test data')

        self._fuse_cycle(delta_create, create_file)

        kernel_path = os.path.join(self.mount_point, name)
        btime_after_create = read_btime(kernel_path)
        self.assertIsNotNone(btime_after_create,
                             'btime should be readable after creation')

        now = int(time.time())
        expected_btime = now + int(delta_create.total_seconds())
        self.assertLessEqual(
            abs(btime_after_create - expected_btime), 5,
            f'btime after create={btime_after_create} '
            f'expected≈{expected_btime}')

        # ── 2. Remount with DIFFERENT delta, then os.utime() ──────────
        delta_utime = timedelta(hours=+3)

        def modify_file(mount):
            target_ts = time.time()
            os.utime(os.path.join(mount, name),
                     (target_ts, target_ts))

        self._fuse_cycle(delta_utime, modify_file)

        # ── 3. Verify btime did NOT change ────────────────────────────
        btime_after_utime = read_btime(kernel_path)
        self.assertIsNotNone(btime_after_utime,
                             'btime should be readable after utime')

        drift = btime_after_utime - btime_after_create
        self.assertEqual(
            drift, 0,
            f'btime changed by {drift}s after os.utime() under '
            f'FUSE+faketime (from {btime_after_create} to '
            f'{btime_after_utime}). This means os.utime() on existing '
            f'files CAN modify btime, contradicting the hypothesis.')

        # Cleanup
        run(['sudo', 'rm', '-f', kernel_path])


if __name__ == '__main__':
    unittest.main()
