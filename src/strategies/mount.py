"""Mount/unmount strategies with auto-detection of source type."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path


class MountError(Exception):
    """Raised when mounting or unmounting fails."""


class MountStrategy(ABC):
    """Strategy for mounting a source and providing access to its filesystem."""

    name: str
    label: str

    @abstractmethod
    def mount(self) -> tuple[str, str]:
        """Mount and return (device_path, mount_point)."""

    @abstractmethod
    def unmount(self):
        """Unmount and clean up resources."""

    @classmethod
    @abstractmethod
    def can_handle(cls, source: str) -> bool:
        """Whether this strategy can handle the given source path."""

    @classmethod
    @abstractmethod
    def required_tools(cls) -> tuple[str, ...]:
        """External executables needed."""


class AlreadyMountedStrategy(MountStrategy):
    """Source is already mounted at the given directory. No-op."""

    name = 'already_mounted'
    label = 'Already mounted (no-op)'

    def __init__(self, path: str):
        self._path = path

    def mount(self) -> tuple[str, str]:
        from btime import _resolve_device, _resolve_mount_point
        dev = _resolve_device(self._path)
        mp = _resolve_mount_point(self._path)
        if not mp:
            raise MountError(f"Could not resolve mount point for {self._path}")
        return (dev or '', mp)

    def unmount(self):
        pass

    @classmethod
    def can_handle(cls, source: str) -> bool:
        return os.path.isdir(source)

    @classmethod
    def required_tools(cls) -> tuple[str, ...]:
        return ()


class ImageMountStrategy(MountStrategy):
    """Create a loop device from an image file and mount it.

    Uses udisksctl for loop-setup and mount (no sudo needed).  On mount-
    path collision (udisksctl returns an existing mount from a different
    device with the same volume serial) falls through to ``sudo mount``
    to a unique tempdir.  Falls back to ``sudo losetup + sudo mount``.
    """

    name = 'image'
    label = 'Image file (loop device)'

    def __init__(self, img_path: str):
        self._img_path = img_path
        self._loop_dev = None
        self._mount_point = None

    def mount(self) -> tuple[str, str]:
        result = self._via_udisksctl()
        if result is not None:
            self._loop_dev, self._mount_point = result
            return result
        result = self._via_sudo()
        if result is not None:
            self._loop_dev, self._mount_point = result
            return result
        raise MountError("Could not set up loop device (udisksctl+sudo failed)")

    @classmethod
    def _find_mount(cls, loop_dev: str) -> str | None:
        """Return the mount point for *loop_dev* from mountinfo, or None."""
        minor = loop_dev.lstrip('/dev/loop')
        needle = f' 7:{minor} '
        try:
            with open('/proc/self/mountinfo') as f:
                for line in f:
                    if needle in line:
                        parts = line.split()
                        if len(parts) >= 5:
                            return parts[4]
        except OSError:
            pass
        return None

    @staticmethod
    def _existing_mount_points() -> set[str]:
        """Return mount paths currently occupied by loop devices."""
        mps: set[str] = set()
        try:
            with open('/proc/self/mountinfo') as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 5:
                        major = parts[2].split(':')[0]
                        if major == '7':
                            mps.add(parts[4])
        except OSError:
            pass
        return mps

    def _via_udisksctl(self) -> tuple[str, str] | None:
        occupied_before = self._existing_mount_points()
        try:
            r = subprocess.run(
                ['udisksctl', 'loop-setup', '-f', str(self._img_path),
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
            if r.returncode == 0:
                m = re.search(r'at ([^ \n]+)', r.stdout)
                if not m:
                    subprocess.run(
                        ['sudo', 'losetup', '-d', loop_dev], capture_output=True)
                    return None
                mount_point = m.group(1).rstrip('.')
                if self._mount_has_device(mount_point, loop_dev) \
                        and mount_point not in occupied_before:
                    return (loop_dev, mount_point)
                return self._via_sudo_with(loop_dev)

            # mount failed — auto-mount may have already done it.
            mount_point = self._find_mount(loop_dev)
            if mount_point and self._mount_has_device(mount_point, loop_dev) \
                    and mount_point not in occupied_before:
                return (loop_dev, mount_point)
            return self._via_sudo_with(loop_dev)
        except FileNotFoundError:
            return None
        except OSError:
            return None

    @staticmethod
    def _mount_has_device(mount_point: str, loop_dev: str) -> bool:
        """Return True if *mount_point* is mounted exclusively from *loop_dev*.

        Checks ALL mountinfo entries at the given mount point to detect
        mount shadowing — a second device mounted at the same path would
        mean our device does not exclusively own the mount.
        """
        minor = loop_dev.lstrip('/dev/loop')
        our_dev = f'7:{minor}'
        try:
            with open('/proc/self/mountinfo') as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 5 and parts[4] == mount_point.rstrip('/'):
                        if parts[2] != our_dev:
                            return False
        except OSError:
            return True
        return True

    def _via_sudo(self) -> tuple[str, str] | None:
        try:
            r = subprocess.run(
                ['sudo', 'losetup', '-f', '--show', str(self._img_path)],
                capture_output=True, text=True)
            if r.returncode != 0:
                return None
            loop_dev = r.stdout.strip()
            # Verify the backing file matches our image (TOCTOU guard).
            # Two parallel losetup -f calls could both get the same device;
            # the last one wins.  If we lost the race, retry.
            back = self._read_backing_file(loop_dev)
            while back is not None and back != str(self._img_path):
                r = subprocess.run(
                    ['sudo', 'losetup', '-f', '--show', str(self._img_path)],
                    capture_output=True, text=True)
                if r.returncode != 0:
                    return None
                loop_dev = r.stdout.strip()
                back = self._read_backing_file(loop_dev)
            return self._via_sudo_with(loop_dev)
        except FileNotFoundError:
            return None

    @staticmethod
    def _read_backing_file(loop_dev: str) -> str | None:
        """Read the backing file path for *loop_dev* via sysfs."""
        dev_name = loop_dev.lstrip('/dev/')
        r = subprocess.run(
            ['cat', f'/sys/block/{dev_name}/loop/backing_file'],
            capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return r.stdout.strip() or None
        r2 = subprocess.run(
            ['sudo', 'losetup', '-n', '-O', 'BACK-FILE', loop_dev],
            capture_output=True, text=True, timeout=5)
        return r2.stdout.strip() or None if r2.returncode == 0 else None

    def _via_sudo_with(self, loop_dev: str) -> tuple[str, str] | None:
        """Mount an already-allocated loop device via ``sudo mount``.

        This avoids the TOCTOU race in ``_via_sudo`` where ``losetup -f``
        can return a device that another process grabbed between the
        ``udisksctl mount`` failure and the fallback ``losetup -f --show``.
        """
        import tempfile
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
                return (loop_dev, mount_point)
        mount_exfat = shutil.which('mount.exfat-fuse')
        if mount_exfat:
            r = subprocess.run(
                ['sudo', 'env', f'PATH={os.environ["PATH"]}',
                 mount_exfat, loop_dev, mount_point,
                 '-o', f'uid={uid}', '-o', f'gid={gid}'],
                capture_output=True, text=True)
            if r.returncode == 0:
                return (loop_dev, mount_point)
        subprocess.run(['sudo', 'losetup', '-d', loop_dev], capture_output=True)
        os.rmdir(mount_point)
        return None

    def unmount(self):
        if self._loop_dev:
            from loop_device import teardown_loop_device
            teardown_loop_device(self._loop_dev, self._mount_point)
            self._loop_dev = None
            self._mount_point = None

    @classmethod
    def can_handle(cls, source: str) -> bool:
        return Path(source).is_file()

    @classmethod
    def required_tools(cls) -> tuple[str, ...]:
        return ('losetup', 'sudo', 'mount', 'umount')


def detect_strategy(source: str) -> MountStrategy:
    """Auto-detect the best mount strategy for *source*.

    *source* can be:
    - A directory (already mounted)
    - A block device path (e.g., ``/dev/sdb1``)
    - An image file (e.g., ``sdcard.img``)

    Returns the first matching strategy.
    """
    for cls in (AlreadyMountedStrategy, ImageMountStrategy):
        if cls.can_handle(source):
            return cls(source)
    raise MountError(f"Don't know how to mount {source}")


REGISTRY: dict[str, type[MountStrategy]] = {
    AlreadyMountedStrategy.name: AlreadyMountedStrategy,
    ImageMountStrategy.name: ImageMountStrategy,
}
