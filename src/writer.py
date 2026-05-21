"""Write-only facade accepting preset target times and dispatching to low-level I/O."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import btime
import media
from exiftool_session import ExifToolSession
from options import BTIME_OFF, BTIME_EXFAT_RAW
from strategies.mtime import (
    MtimeStrategy,
    OsUtimeMtimeStrategy,
    ExfatRawMtimeStrategy,
    SkipMtimeStrategy,
)


@dataclass
class WriteJob:
    path: Path
    target_embedded: datetime | None
    target_mtime: datetime | None


def _normalize_btime(value):
    if value is None or value is False:
        return []
    if isinstance(value, str):
        if value == BTIME_OFF:
            return []
        return [value]
    return list(value)


@dataclass
class WriteSummary:
    written: int = 0
    skipped: int = 0
    errors: list[str] | None = None


class Writer:
    """Writes target times to files. No computation — pure dispatch."""

    def __init__(
        self,
        target_dir: Path,
        fix_btime: str | list[str] | tuple[str] = BTIME_OFF,
        delta: timedelta | None = None,
        dry_run: bool = False,
        session: 'ExifToolSession | None' = None,
    ):
        self.target_dir = target_dir
        self.dry_run = dry_run
        self._b_method: str | None = None
        self._b_ctx: dict = {}
        self._delta = delta
        self._session = session
        self._mtime_strategy: MtimeStrategy | None = None

        methods = _normalize_btime(fix_btime)
        if methods:
            fs = btime.detect_fs(target_dir)
            self._b_method, self._b_ctx = btime.chain_setup(
                methods, target_dir, fs, delta or timedelta(), dry_run)

    def _btime_handles_mtime(self) -> bool:
        return self._b_method == BTIME_EXFAT_RAW

    def _resolve_mtime_strategy(self) -> MtimeStrategy:
        if self._btime_handles_mtime():
            return SkipMtimeStrategy()
        fs = None
        try:
            fs = btime.detect_fs(self.target_dir)
        except Exception:
            pass
        if fs in ('exfat', 'fuse', 'exfat_raw') and self._b_method != BTIME_EXFAT_RAW:
            return ExfatRawMtimeStrategy()
        return OsUtimeMtimeStrategy()

    @property
    def mtime_strategy(self) -> MtimeStrategy:
        if self._mtime_strategy is None:
            self._mtime_strategy = self._resolve_mtime_strategy()
        return self._mtime_strategy

    def write(self, job: WriteJob) -> bool:
        if self.dry_run:
            return True

        ok = bool(job.target_embedded
                  and self._session
                  and self._session.write_embedded(job.path, job.target_embedded))
        if btime.needs_processing_after(self._b_method) and job.target_mtime is not None:
            btime.fix_file(self._b_method, job.path, job.target_mtime, self._b_ctx, self.dry_run)
        if job.target_mtime is not None and not self._btime_handles_mtime():
            self.mtime_strategy.write_mtime(job.path, job.target_mtime)
        return ok

    def write_embedded_only(self, job: WriteJob) -> bool:
        if self.dry_run or not job.target_embedded or not self._session:
            return False
        return self._session.write_embedded(job.path, job.target_embedded)

    def write_mtime_only(self, job: WriteJob) -> bool:
        if self.dry_run or job.target_mtime is None:
            return False
        if self._btime_handles_mtime():
            return True
        return self.mtime_strategy.write_mtime(job.path, job.target_mtime)

    def write_btime_only(self, job: WriteJob) -> bool:
        if self.dry_run or job.target_mtime is None:
            return False
        if self._b_method:
            import sys as _sys
            _sys.stderr.write(f'[dbg] write_btime_only: {job.path.name} '
                             f'target_mtime={job.target_mtime!r} '
                             f'target_ts={int(job.target_mtime.timestamp())} '
                             f'b_method={self._b_method!r}\n')
            btime.fix_file(self._b_method, job.path, job.target_mtime, self._b_ctx, self.dry_run)
            return True
        return False

    def write_all(self, jobs: list[WriteJob]) -> WriteSummary:
        summary = WriteSummary()
        if not jobs:
            return summary

        if not self.dry_run and self._session:
            emb_pairs = [(j.path, j.target_embedded) for j in jobs
                         if j.target_embedded is not None]
            batch_ok = self._session.write_embedded_batch(emb_pairs)
        else:
            batch_ok = True

        for job in jobs:
            if not self.dry_run and job.target_embedded is not None and not batch_ok:
                (summary.errors or []).append(str(job.path))
                continue

            summary.written += 1

            if btime.needs_processing_after(self._b_method) and job.target_mtime is not None:
                btime.fix_file(self._b_method, job.path, job.target_mtime, self._b_ctx, self.dry_run)
            if job.target_mtime is not None and not self._btime_handles_mtime():
                self.mtime_strategy.write_mtime(job.path, job.target_mtime)

        return summary

    def close(self):
        if self._b_method and (btime.needs_processing_before(self._b_method) or self._b_method == 'clock'):
            btime.teardown(self._b_method, self._b_ctx, self.dry_run)
        if self._b_method == BTIME_EXFAT_RAW and not self.dry_run:
            mp = btime._resolve_mount_point(self.target_dir)
            dev = btime._resolve_device(self.target_dir)
            if mp and dev:
                subprocess.run(['sudo', 'umount', mp],
                               capture_output=True, timeout=15)
                uid = os.getuid()
                gid = os.getgid()
                import shutil as _shutil
                import time as _time
                for attempt in range(3):
                    for fs_type in ('exfat', 'fuse.exfat', 'auto'):
                        r = subprocess.run(
                            ['sudo', 'mount', '-t', fs_type,
                             '-o', f'uid={uid},gid={gid}',
                             dev, mp],
                            capture_output=True, text=True, timeout=15)
                        if r.returncode == 0:
                            return
                    mount_exfat = _shutil.which('mount.exfat-fuse')
                    if mount_exfat:
                        r = subprocess.run(
                            ['sudo', 'env', f'PATH={os.environ.get("PATH", "")}',
                             mount_exfat, dev, mp,
                             '-o', f'uid={uid}', '-o', f'gid={gid}'],
                            capture_output=True, timeout=15)
                        if r.returncode == 0:
                            return
                    if attempt < 2:
                        _time.sleep(1)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
