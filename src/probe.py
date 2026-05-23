"""System capability probes for btime readback, block devices, and exFAT."""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class BtimeProbeResult:
    """Result of probing birth-time readability on a filesystem."""

    path: str
    fs_type: str | None
    stat_btime: int | None
    statx_btime: int | None
    statx_supported: bool | None
    utime_works: bool | None


@dataclass
class ExfatProbeResult:
    """Result of probing exFAT btime readability and block device capabilities."""

    supported: bool | None
    stat_method: bool | None
    statx_method: bool | None
    raw_read_method: bool | None
    utime_on_exfat: bool | None
    dd_nocache: bool | None
    blockdev_flush: bool | None
    reason: str = ''


# ── stat / statx probes ─────────────────────────────────────────


def _has_statx() -> bool:
    try:
        libc = ctypes.CDLL(ctypes.util.find_library('c'), use_errno=True)
        libc.statx.restype = ctypes.c_int
        return True
    except Exception:
        return False


def probe_stat_btime(path: str) -> int | None:
    """Return birth time (epoch seconds) via ``stat -c '%W'``, or None."""
    r = subprocess.run(
        ['stat', '-c', '%W', path],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode != 0:
        return None
    val = r.stdout.strip()
    try:
        return int(val)
    except ValueError:
        return None


def probe_statx_btime(path: str) -> tuple[int | None, bool | None]:
    """Return ``(btime_epoch, stx_mask_has_btime)`` via ``statx()``."""
    try:
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
        libc = ctypes.CDLL(ctypes.util.find_library('c'), use_errno=True)
        buf = statx_buf()
        ret = libc.statx(-100, path.encode(), 0, STATX_BTIME, ctypes.byref(buf))
        if ret != 0:
            return None, None
        mask_ok = bool(buf.stx_mask & STATX_BTIME)
        return buf.stx_btime.tv_sec, mask_ok
    except Exception:
        return None, None


def probe_utime(path: str) -> bool | None:
    """Check whether os.utime() works on this filesystem."""
    fname = None
    try:
        f = tempfile.NamedTemporaryFile(dir=path, delete=False)
        fname = f.name
        f.close()
        ts = 1234567890.0
        os.utime(fname, (ts, ts))
        st = os.stat(fname)
        return abs(st.st_mtime - ts) < 1.0
    except OSError:
        return False
    except Exception:
        return None
    finally:
        if fname is not None and os.path.exists(fname):
            os.unlink(fname)


# ── General btime probe (any filesystem) ────────────────────────


import btime as _btime  # noqa: E402


def probe_btime(path: str | Path) -> BtimeProbeResult:
    """Probe birth-time readability on the filesystem containing *path*."""
    path = str(path)
    try:
        fs_type = _btime.detect_fs(path)
    except Exception:
        fs_type = None

    stat_btime = probe_stat_btime(path)
    statx_btime, statx_supported = probe_statx_btime(path)
    utime_works = probe_utime(path)
    return BtimeProbeResult(
        path=path,
        fs_type=fs_type,
        stat_btime=stat_btime,
        statx_btime=statx_btime,
        statx_supported=statx_supported,
        utime_works=utime_works,
    )


# ── exFAT-specific probes (need a temp exFAT filesystem) ────────


def _probe_stat_btime_on_exfat(test_file: str) -> bool | None:
    val = probe_stat_btime(test_file)
    return val is not None and val > 0


def _probe_statx_btime_on_exfat(test_file: str) -> bool | None:
    val, _supported = probe_statx_btime(test_file)
    return val is not None and val > 0


def _probe_raw_read_btime_on_exfat(test_file: str) -> bool | None:
    from strategies.exfat_raw import exfat_ops
    val = exfat_ops.read_btime_raw(test_file)
    return val is not None and val > 0


def probe_udisksctl_automount(img_path: str) -> bool | None:
    """Check whether ``udisksctl loop-setup`` auto-mounts on this system.

    Creates a loop device, waits for the async auto-mount (up to 3 s),
    checks mountinfo, cleans up, and returns ``True`` (auto-mount),
    ``False`` (no auto-mount), or ``None`` (udisksctl unavailable or
    setup failed).
    """
    if not shutil.which('udisksctl'):
        return None
    r = subprocess.run(
        ['udisksctl', 'loop-setup', '-f', img_path, '--no-user-interaction'],
        capture_output=True, text=True)
    if r.returncode != 0:
        return None
    m = re.search(r'as (/dev/loop\d+)', r.stdout)
    if not m:
        return None
    loop_dev = m.group(1)
    minor = loop_dev.lstrip('/dev/loop')
    automount = False
    try:
        for _ in range(6):  # up to 3 s
            with open('/proc/self/mountinfo') as f:
                if any(f' 7:{minor} ' in line for line in f):
                    automount = True
                    break
            time.sleep(0.5)
    except OSError:
        pass
    finally:
        subprocess.run(
            ['udisksctl', 'unmount', '-b', loop_dev, '--no-user-interaction'],
            capture_output=True)
    return automount


def _probe_dd_nocache(device: str) -> bool | None:
    try:
        r = subprocess.run(
            ['sudo', 'dd', f'if={device}', 'bs=512', 'count=1',
             'iflag=nocache', 'status=none'],
            capture_output=True, timeout=10)
        return r.returncode == 0 and len(r.stdout) == 512
    except Exception:
        return None


def _probe_blockdev_flush(device: str) -> bool | None:
    try:
        r = subprocess.run(
            ['sudo', 'blockdev', '--flushbufs', device],
            capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return None


def probe_exfat_btime() -> ExfatProbeResult:
    """Create a temp exFAT filesystem, mount it, and probe all capabilities."""
    from loop_device import setup_loop_device, teardown_loop_device, LoopDeviceError

    if not shutil.which('mkfs.exfat'):
        return ExfatProbeResult(supported=None, reason='mkfs.exfat not found',
                                stat_method=None, statx_method=None, raw_read_method=None,
                                utime_on_exfat=None, dd_nocache=None, blockdev_flush=None)

    tmp_img = None
    loop_dev = None
    mount_point = None
    try:
        fd, tmp_img = tempfile.mkstemp(suffix='.img', prefix='exfat_btime_probe_')
        os.close(fd)
        os.truncate(tmp_img, 64 * 1024 * 1024)

        r = subprocess.run(['mkfs.exfat', tmp_img], capture_output=True, timeout=30)
        if r.returncode != 0:
            return ExfatProbeResult(supported=None,
                                    reason=f'mkfs.exfat failed: {r.stderr.strip()}',
                                    stat_method=None, statx_method=None, raw_read_method=None,
                                    utime_on_exfat=None, dd_nocache=None, blockdev_flush=None)

        try:
            loop_dev, mount_point = setup_loop_device(tmp_img)
        except LoopDeviceError as e:
            return ExfatProbeResult(supported=None, reason=str(e),
                                    stat_method=None, statx_method=None, raw_read_method=None,
                                    utime_on_exfat=None, dd_nocache=None, blockdev_flush=None)

        test_file = os.path.join(mount_point, 'probe.bin')
        subprocess.run(['sudo', 'touch', test_file], capture_output=True, timeout=15)
        subprocess.run(['sudo', 'chmod', '644', test_file], capture_output=True, timeout=15)

        subprocess.run(['sync'])
        subprocess.run(['sudo', 'sh', '-c', 'echo 3 > /proc/sys/vm/drop_caches'],
                       capture_output=True, timeout=15)

        stat_ok = _probe_stat_btime_on_exfat(test_file)
        statx_ok = _probe_statx_btime_on_exfat(test_file)
        raw_ok = _probe_raw_read_btime_on_exfat(test_file)
        supported = stat_ok or statx_ok or raw_ok

        utime_try = 1234567890.0
        try:
            os.utime(test_file, (utime_try, utime_try))
            st = os.stat(test_file)
            utime_on_exfat = abs(st.st_mtime - utime_try) < 1.0
        except OSError:
            utime_on_exfat = False
        except Exception:
            utime_on_exfat = None

        dd_nocache = _probe_dd_nocache(loop_dev)
        blockdev_flush = _probe_blockdev_flush(loop_dev)

        return ExfatProbeResult(
            supported=supported,
            stat_method=stat_ok,
            statx_method=statx_ok,
            raw_read_method=raw_ok,
            utime_on_exfat=utime_on_exfat,
            dd_nocache=dd_nocache,
            blockdev_flush=blockdev_flush,
        )

    except FileNotFoundError as e:
        return ExfatProbeResult(supported=None, reason=f'missing tool: {e.name}',
                                stat_method=None, statx_method=None, raw_read_method=None,
                                utime_on_exfat=None, dd_nocache=None, blockdev_flush=None)
    except Exception as e:
        return ExfatProbeResult(supported=None, reason=str(e),
                                stat_method=None, statx_method=None, raw_read_method=None,
                                utime_on_exfat=None, dd_nocache=None, blockdev_flush=None)
    finally:
        if loop_dev:
            teardown_loop_device(loop_dev, mount_point)
        if tmp_img and os.path.exists(tmp_img):
            os.unlink(tmp_img)
        if mount_point and os.path.exists(mount_point):
            os.rmdir(mount_point)
