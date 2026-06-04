"""Mtime writing strategies with capability-based selection."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path


class MtimeStrategy(ABC):
    """Abstract base for an mtime writing strategy."""

    name: str
    label: str

    @abstractmethod
    def write_mtime(self, path: str | Path, dt: datetime) -> bool:
        ...

    @classmethod
    @abstractmethod
    def compatible_filesystems(cls) -> tuple[str, ...]:
        ...

    @classmethod
    @abstractmethod
    def requires_utime(cls) -> bool:
        """True when this strategy uses os.utime() and needs it to work."""


class OsUtimeMtimeStrategy(MtimeStrategy):
    name = 'os_utime'
    label = 'os.utime()'

    def write_mtime(self, path: str | Path, dt: datetime) -> bool:
        from media import write_mtime
        write_mtime(path, dt)
        return True

    @classmethod
    def compatible_filesystems(cls) -> tuple[str, ...]:
        return ('exfat', 'ext4', 'fuse', 'exfat_raw', 'debugfs')

    @classmethod
    def requires_utime(cls) -> bool:
        return True


class ExfatRawMtimeStrategy(MtimeStrategy):
    name = 'exfat_raw_mtime'
    label = 'exFAT raw block (mtime only)'

    def __init__(self, ops=None):
        from exfat_raw import exfat_ops
        self._ops = ops or exfat_ops

    def write_mtime(self, path: str | Path, dt: datetime) -> bool:
        orig_btime_epoch = self._ops.read_btime_raw(str(path))
        if orig_btime_epoch is None:
            return False
        orig_btime = datetime.fromtimestamp(orig_btime_epoch, tz=timezone.utc)
        self._ops.fix_exfat_raw(str(path), dt, dry_run=False, btime_dt=orig_btime)
        return True

    @classmethod
    def compatible_filesystems(cls) -> tuple[str, ...]:
        return ('exfat_raw', 'fuse')

    @classmethod
    def requires_utime(cls) -> bool:
        return False


class SkipMtimeStrategy(MtimeStrategy):
    name = 'skip'
    label = 'skip (handled by btime)'

    def write_mtime(self, path: str | Path, dt: datetime) -> bool:
        return True

    @classmethod
    def compatible_filesystems(cls) -> tuple[str, ...]:
        return ('exfat_raw',)

    @classmethod
    def requires_utime(cls) -> bool:
        return False


MTIME_REGISTRY: dict[str, type[MtimeStrategy]] = {
    OsUtimeMtimeStrategy.name: OsUtimeMtimeStrategy,
    ExfatRawMtimeStrategy.name: ExfatRawMtimeStrategy,
    SkipMtimeStrategy.name: SkipMtimeStrategy,
}
