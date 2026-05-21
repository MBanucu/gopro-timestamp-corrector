"""Write-only facade accepting preset target times and dispatching to low-level I/O.

This module has no computation logic. It receives a list of WriteJob
objects (file + target times) and writes them to disk via media.py and btime.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

import media
import btime
from exiftool_session import ExifToolSession
from options import BTIME_OFF, BTIME_EXFAT_RAW


@dataclass
class WriteJob:
    path: Path
    target_embedded: datetime | None
    target_mtime: datetime | None


def _normalize_btime(value):
    """Normalise *value* to an ordered list of btime method names.

    Accepts ``'off'`` (or None/False), a single method string, or an
    iterable of strings.  Returns a list of method names (possibly empty).
    """
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

        methods = _normalize_btime(fix_btime)
        if methods:
            fs = btime.detect_fs(target_dir)
            self._b_method, self._b_ctx = btime.chain_setup(
                methods, target_dir, fs, delta or timedelta(), dry_run)

    def _btime_handles_mtime(self) -> bool:
        return self._b_method == BTIME_EXFAT_RAW

    def write(self, job: WriteJob) -> bool:
        """Write a single job to embedded metadata, mtime, and optionally btime."""
        if self.dry_run:
            return True

        ok = bool(job.target_embedded
                  and self._session
                  and self._session.write_embedded(job.path, job.target_embedded))
        if btime.needs_processing_after(self._b_method) and job.target_mtime is not None:
            btime.fix_file(self._b_method, job.path, job.target_mtime, self._b_ctx, self.dry_run)
        if job.target_mtime is not None and not self._btime_handles_mtime():
            media.write_mtime(job.path, job.target_mtime)
        return ok

    def write_embedded_only(self, job: WriteJob) -> bool:
        """Write only embedded EXIF/QuickTime metadata."""
        if self.dry_run or not job.target_embedded or not self._session:
            return False
        return self._session.write_embedded(job.path, job.target_embedded)

    def write_mtime_only(self, job: WriteJob) -> bool:
        """Write only filesystem modification time.

        When ``exfat_raw`` is the active btime method it handles both
        mtime and btime in a single raw-block access — skip the
        separate ``os.utime()`` call (which fails with EPERM on the
        kernel exfat driver).
        """
        if self.dry_run or job.target_mtime is None:
            return False
        if self._btime_handles_mtime():
            return True
        media.write_mtime(job.path, job.target_mtime)
        return True

    def write_btime_only(self, job: WriteJob) -> bool:
        """Write only filesystem birth time (needs btime setup done externally)."""
        if self.dry_run or job.target_mtime is None:
            return False
        if self._b_method:
            btime.fix_file(self._b_method, job.path, job.target_mtime, self._b_ctx, self.dry_run)
            return True
        return False

    def write_all(self, jobs: list[WriteJob]) -> WriteSummary:
        """Write multiple jobs. Returns summary."""
        summary = WriteSummary()
        if not jobs:
            return summary

        # ── Batch-write embedded times (via persistent exiftool) ──
        if not self.dry_run and self._session:
            emb_pairs = [(j.path, j.target_embedded) for j in jobs
                         if j.target_embedded is not None]
            batch_ok = self._session.write_embedded_batch(emb_pairs)
        else:
            batch_ok = True

        # ── Per-file: btime + mtime ──────────────────────────────
        # ``exfat_raw`` writes both timestamps in one raw-block access;
        # the separate media.write_mtime is skipped for it.
        for job in jobs:
            if not self.dry_run and job.target_embedded is not None and not batch_ok:
                (summary.errors or []).append(str(job.path))
                continue

            summary.written += 1

            if btime.needs_processing_after(self._b_method) and job.target_mtime is not None:
                btime.fix_file(self._b_method, job.path, job.target_mtime, self._b_ctx, self.dry_run)
            if job.target_mtime is not None and not self._btime_handles_mtime():
                media.write_mtime(job.path, job.target_mtime)

        return summary

    def close(self):
        """Tear down btime if needed."""
        if self._b_method and (btime.needs_processing_before(self._b_method) or self._b_method == 'clock'):
            btime.teardown(self._b_method, self._b_ctx, self.dry_run)
        # Remount to flush the exFAT driver's private metadata cache.
        # _fix_exfat_raw writes via dd (bypasses the driver), so the
        # driver's cache becomes stale.  mount -o remount clears it.
        if self._b_method == BTIME_EXFAT_RAW and not self.dry_run:
            try:
                mp = btime._resolve_mount_point(self.target_dir)
                if mp:
                    subprocess.run(['sudo', 'mount', '-o', 'remount', mp],
                                   capture_output=True, timeout=15)
            except Exception:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
