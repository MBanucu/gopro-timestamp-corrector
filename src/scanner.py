"""Scan mounted devices for GoPro media directories."""

from __future__ import annotations

from pathlib import Path

_PSEUDO_FS = frozenset({
    'proc', 'sys', 'sysfs', 'tmpfs', 'devtmpfs', 'devpts',
    'cgroup', 'cgroup2', 'pstore', 'securityfs', 'selinuxfs',
    'autofs', 'debugfs', 'tracefs', 'ramfs', 'hugetlbfs',
    'mqueue', 'bpf', 'configfs', 'efivarfs', 'fuse.gvfsd-fuse',
    'fusectl', 'overlay', 'squashfs', 'nsfs', 'rpc_pipefs',
})

_GOPRO_PATTERNS = ('DCIM/[0-9]*GOPRO*', 'dcim/[0-9]*gopro*')


def _get_mount_points() -> list[Path]:
    """Parse /proc/mounts for real (non-pseudo) mount points."""
    mounts: list[Path] = []
    try:
        with open('/proc/mounts') as f:
            for line in f:
                parts = line.split()
                if len(parts) < 3:
                    continue
                dev, mount_point, fstype = parts[0], parts[1], parts[2]
                if fstype in _PSEUDO_FS:
                    continue
                if dev in ('rootfs', 'none'):
                    continue
                mounts.append(Path(mount_point))
    except OSError:
        pass
    return mounts


def find_gopro_paths() -> list[Path]:
    """Scan all mount points for GoPro DCIM directories.

    Returns sorted list of unique absolute paths to GoPro media
    directories (e.g. ``/media/user/SD_CARD/DCIM/100GOPRO``).
    """
    found: set[Path] = set()

    for mp in _get_mount_points():
        for pattern in _GOPRO_PATTERNS:
            try:
                for p in mp.glob(pattern):
                    if p.is_dir():
                        found.add(p.resolve())
            except PermissionError:
                continue

    return sorted(found)
