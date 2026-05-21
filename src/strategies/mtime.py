"""Mtime writing strategies with capability-based selection."""

from __future__ import annotations

import os as _os
import struct
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
        return ('exfat', 'ext4', 'fuse', 'exfat_raw', 'debugfs', 'clock')

    @classmethod
    def requires_utime(cls) -> bool:
        return True


class ExfatRawMtimeStrategy(MtimeStrategy):
    name = 'exfat_raw_mtime'
    label = 'exFAT raw block (mtime only)'

    def write_mtime(self, path: str | Path, dt: datetime) -> bool:
        from strategies.exfat_raw import _fix_exfat_raw, _exfat_parse_boot, _exfat_find_file_entry, _exfat_decode_time
        from btime import _resolve_device

        device = _resolve_device(path)
        if not device:
            return False

        boot = _exfat_parse_boot(device)
        if not boot:
            return False

        entry = _exfat_find_file_entry(boot, device, str(path))
        if not entry:
            return False

        time_word = struct.unpack_from('<H', entry, 0x0C)[0]
        date_word = struct.unpack_from('<H', entry, 0x0E)[0]
        time_ms = entry[0x16]
        orig_btime = _exfat_decode_time(time_word, date_word, time_ms)

        _fix_exfat_raw(str(path), dt, dry_run=False, btime_dt=orig_btime)
        return True

    @classmethod
    def compatible_filesystems(cls) -> tuple[str, ...]:
        return ('exfat_raw', 'fuse', 'clock')

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
