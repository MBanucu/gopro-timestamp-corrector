"""Loop device setup/teardown — delegates to mount strategies."""

import os
import subprocess
import time

from strategies.mount import MountError as LoopDeviceError
from strategies.mount import ImageMountStrategy


# ── helpers ────────────────────────────────────────────────────────


def _loop_minor(loop_dev: str) -> str:
    """Extract the minor number from a /dev/loopN path."""
    return loop_dev.lstrip('/dev/loop')


def _mount_points_for(loop_dev: str) -> list[str]:
    """Return all mount points for *loop_dev* from mountinfo."""
    minor = _loop_minor(loop_dev)
    needle = f' 7:{minor} '
    mps: list[str] = []
    try:
        with open('/proc/self/mountinfo') as f:
            for line in f:
                if needle in line:
                    parts = line.split()
                    if len(parts) >= 5:
                        mps.append(parts[4])
    except OSError:
        pass
    return mps


def _loop_dev_from_mount_point(mount_point: str) -> str | None:
    """Return the loop device (e.g. /dev/loop5) mounted at *mount_point*."""
    try:
        with open('/proc/self/mountinfo') as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 5 and parts[4] == mount_point.rstrip('/'):
                    dev = parts[2]
                    major, minor = dev.split(':')
                    if major == '7':  # loop major
                        return f'/dev/loop{minor}'
    except OSError:
        pass
    return None


def _detach_loop(loop_dev: str) -> bool:
    """Detach loop device, retrying once if busy."""
    r = subprocess.run(['sudo', 'losetup', '-d', loop_dev],
                       capture_output=True, text=True)
    if r.returncode == 0:
        return True
    # Device might be busy — wait briefly and retry
    time.sleep(0.3)
    r = subprocess.run(['sudo', 'losetup', '-d', loop_dev],
                       capture_output=True, text=True)
    return r.returncode == 0


# ── public API ─────────────────────────────────────────────────────


def setup_loop_device(img_path: str) -> tuple[str, str]:
    """Set up loop device and mount an image.

    Uses udisksctl for loop-setup + mount (no sudo needed).  On mount-path
    collision falls through to ``sudo mount`` to a unique tempdir.
    Returns (loop_dev, mount_point).
    Raises LoopDeviceError on failure.
    """
    strategy = ImageMountStrategy(img_path)
    return strategy.mount()


def teardown_loop_device(loop_dev: str, mount_point: str | None = None):
    """Unmount all mount points of *loop_dev* and detach it.

    1. Unmounts *mount_point* if given (most common path).
    2. Scans ``/proc/self/mountinfo`` for any other mount points on the
       same device (handles split/dual mounts).
    3. Falls back to lazy unmount (``umount -l``) for stubborn mounts.
    4. Detaches the loop device (with one retry).
    5. Clears the exfat_io backing-file cache.
    """
    if not loop_dev:
        return

    minor = _loop_minor(loop_dev)

    # 1 — unmount provided mount_point
    tried: set[str] = set()
    if mount_point:
        r = subprocess.run(['sudo', 'umount', mount_point],
                           capture_output=True, text=True)
        if r.returncode == 0:
            tried.add(mount_point.rstrip('/'))

    # 2 — unmount any remaining mount points on this device
    remaining = [m for m in _mount_points_for(loop_dev) if m not in tried]
    for mnt in remaining:
        subprocess.run(['sudo', 'umount', mnt],
                       capture_output=True, text=True)
        tried.add(mnt)

    # 3 — lazy fallback for anything still stuck
    remaining = [m for m in _mount_points_for(loop_dev) if m not in tried]
    if remaining:
        for mnt in remaining:
            subprocess.run(['sudo', 'umount', '-l', mnt],
                           capture_output=True, text=True)

    # 4 — detach loop device
    _detach_loop(loop_dev)

    # 5 — clear backing-file cache
    from strategies.exfat_raw import exfat_io
    exfat_io.clear_cache(loop_dev)


def cleanup_all_loop_devices():
    """Unmount and detach ALL loop devices on the system.

    Intended for test teardown and emergency cleanup.
    Scans ``/proc/self/mountinfo`` and ``losetup -a`` so it catches
    devices even when the original test context is lost.
    """
    seen: set[str] = set()

    # Collect all unique loop devices from mountinfo and losetup -a
    for mnt_dev in _get_loops_from_mountinfo():
        if mnt_dev not in seen:
            teardown_loop_device(mnt_dev)
            seen.add(mnt_dev)

    for loop_dev in _get_loops_from_losetup():
        if loop_dev not in seen:
            teardown_loop_device(loop_dev)
            seen.add(loop_dev)


# ── internal helpers for cleanup_all ───────────────────────────────


def _get_loops_from_mountinfo() -> list[str]:
    """Return list of loop device paths found in mountinfo."""
    devs: list[str] = []
    try:
        with open('/proc/self/mountinfo') as f:
            for line in f:
                parts = line.split()
                if len(parts) < 3:
                    continue
                major, minor = parts[2].split(':')
                if major == '7':
                    devs.append(f'/dev/loop{minor}')
    except OSError:
        pass
    return list(dict.fromkeys(devs))  # dedup, preserve order


def _get_loops_from_losetup() -> list[str]:
    """Return list of loop device paths from ``losetup -a``."""
    r = subprocess.run(['losetup', '-a'], capture_output=True, text=True)
    if r.returncode != 0:
        return []
    devs: list[str] = []
    for line in r.stdout.splitlines():
        dev = line.split(':')[0].strip()
        if dev:
            devs.append(dev)
    return devs
