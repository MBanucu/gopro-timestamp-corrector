"""Loop device setup/teardown with udisksctl + sudo fallback."""

import os
import re
import shutil
import subprocess
import tempfile


class LoopDeviceError(Exception):
    """Raised when loop device setup or teardown fails."""


def _loop_via_udisksctl(img_path: str) -> tuple[str, str] | None:
    """Try udisksctl loop-setup + mount. Returns (loop_dev, mount_point) or None."""
    try:
        r = subprocess.run(
            ['udisksctl', 'loop-setup', '-f', str(img_path),
             '--no-user-interaction'],
            capture_output=True, text=True)
        if r.returncode != 0:
            return None
        m = re.search(r'as (/dev/loop\d+)', r.stdout)
        if not m:
            return None
        loop_dev = m.group(1)

        r = subprocess.run(
            ['udisksctl', 'mount', '-b', loop_dev, '--no-user-interaction'],
            capture_output=True, text=True)
        if r.returncode != 0:
            subprocess.run(['sudo', 'losetup', '-d', loop_dev], capture_output=True)
            return None
        m = re.search(r'at ([^ \n]+)', r.stdout)
        if not m:
            subprocess.run(['sudo', 'losetup', '-d', loop_dev], capture_output=True)
            return None
        return (loop_dev, m.group(1).rstrip('.'))
    except FileNotFoundError:
        return None


def _loop_via_sudo(img_path: str) -> tuple[str, str] | None:
    """Fallback: sudo losetup + sudo mount. Returns (loop_dev, mount_point) or None."""
    try:
        r = subprocess.run(
            ['sudo', 'losetup', '-f', '--show', str(img_path)],
            capture_output=True, text=True)
        if r.returncode != 0:
            return None
        loop_dev = r.stdout.strip()

        mount_point = tempfile.mkdtemp(prefix='gopro_mnt_')

        uid = os.getuid()
        gid = os.getgid()

        for fs_type in ('exfat', 'fuse.exfat', 'auto'):
            r = subprocess.run(
                ['sudo', 'mount', '-t', fs_type,
                 '-o', f'uid={uid},gid={gid}',
                 loop_dev, mount_point],
                capture_output=True, text=True)
            if r.returncode == 0:
                break
        if r.returncode != 0:
            mount_exfat = shutil.which('mount.exfat-fuse')
            if mount_exfat:
                r = subprocess.run(
                    ['sudo', 'env', f'PATH={os.environ["PATH"]}',
                     mount_exfat, loop_dev, mount_point,
                     '-o', f'uid={uid}', '-o', f'gid={gid}'],
                    capture_output=True, text=True)
        if r.returncode != 0:
            subprocess.run(['sudo', 'losetup', '-d', loop_dev], capture_output=True)
            os.rmdir(mount_point)
            return None
        return (loop_dev, mount_point)
    except FileNotFoundError:
        return None


def setup_loop_device(img_path: str) -> tuple[str, str]:
    """Set up loop device and mount an image.

    Tries udisksctl first (no sudo), then sudo losetup + sudo mount.
    Returns (loop_dev, mount_point).
    Raises LoopDeviceError on failure.
    """
    result = _loop_via_udisksctl(img_path)
    if result is not None:
        return result
    result = _loop_via_sudo(img_path)
    if result is not None:
        return result
    raise LoopDeviceError("Could not set up loop device (udisksctl+sudo failed)")


def teardown_loop_device(loop_dev: str, mount_point: str | None = None):
    """Unmount and detach loop device."""
    if loop_dev:
        subprocess.run(['sudo', 'umount', loop_dev], capture_output=True)
        subprocess.run(['sudo', 'losetup', '-d', loop_dev], capture_output=True)
