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

    Tries udisksctl first (no sudo), then sudo losetup + sudo mount.
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

    def _via_udisksctl(self) -> tuple[str, str] | None:
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
            if r.returncode != 0:
                # udisksctl mount failed — retry with direct sudo mount
                # instead of detaching + re-allocating (which races with
                # concurrent losetup -f in other processes).
                return self._via_sudo_with(loop_dev)
            m = re.search(r'at ([^ \n]+)', r.stdout)
            if not m:
                subprocess.run(['sudo', 'losetup', '-d', loop_dev], capture_output=True)
                return None
            return (loop_dev, m.group(1).rstrip('.'))
        except FileNotFoundError:
            return None

    def _via_sudo(self) -> tuple[str, str] | None:
        try:
            r = subprocess.run(
                ['sudo', 'losetup', '-f', '--show', str(self._img_path)],
                capture_output=True, text=True)
            if r.returncode != 0:
                return None
            loop_dev = r.stdout.strip()
            return self._via_sudo_with(loop_dev)
        except FileNotFoundError:
            return None

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
            subprocess.run(['sudo', 'umount', self._loop_dev], capture_output=True)
            subprocess.run(['sudo', 'losetup', '-d', self._loop_dev], capture_output=True)
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
