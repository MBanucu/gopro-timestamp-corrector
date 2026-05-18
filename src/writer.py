"""Write-only facade accepting preset target times and dispatching to low-level I/O.

This module has no computation logic. It receives a list of WriteJob
objects (file + target times) and writes them to disk via media.py and btime.py.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

import media
import btime
from options import BTIME_OFF


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
    ):
        self.target_dir = target_dir
        self.dry_run = dry_run
        self._b_method: str | None = None
        self._b_ctx: dict = {}
        self._delta = delta

        methods = _normalize_btime(fix_btime)
        if methods:
            fs = btime.detect_fs(target_dir)
            self._b_method, self._b_ctx = btime.chain_setup(
                methods, target_dir, fs, delta or timedelta(), dry_run)

    def write(self, job: WriteJob) -> bool:
        """Write a single job to embedded metadata, mtime, and optionally btime."""
        if self.dry_run:
            return True

        ok = bool(job.target_embedded and media.write_embedded(job.path, job.target_embedded))
        if job.target_mtime is not None:
            media.write_mtime(job.path, job.target_mtime)
        if btime.needs_processing_after(self._b_method) and job.target_mtime is not None:
            btime.fix_file(self._b_method, job.path, job.target_mtime, self._b_ctx, self.dry_run)
        return ok

    def write_embedded_only(self, job: WriteJob) -> bool:
        """Write only embedded EXIF/QuickTime metadata."""
        if self.dry_run or not job.target_embedded:
            return False
        return media.write_embedded(job.path, job.target_embedded)

    def write_mtime_only(self, job: WriteJob) -> bool:
        """Write only filesystem modification time."""
        if self.dry_run or job.target_mtime is None:
            return False
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

        # ── Batch-write embedded times (exiftool JSON import) ──
        if not self.dry_run:
            emb_pairs = [(j.path, j.target_embedded) for j in jobs
                         if j.target_embedded is not None]
            batch_ok = media.write_embedded_batch(emb_pairs)
        else:
            batch_ok = True

        # ── Per-file: mtime + btime ────────────────────────────
        for job in jobs:
            if not self.dry_run and job.target_embedded is not None and not batch_ok:
                (summary.errors or []).append(str(job.path))
                continue

            summary.written += 1

            if job.target_mtime is not None:
                media.write_mtime(job.path, job.target_mtime)

            if btime.needs_processing_after(self._b_method) and job.target_mtime is not None:
                btime.fix_file(self._b_method, job.path, job.target_mtime, self._b_ctx, self.dry_run)

        return summary

    def close(self):
        """Tear down btime if needed."""
        if self._b_method and (btime.needs_processing_before(self._b_method) or self._b_method == 'clock'):
            btime.teardown(self._b_method, self._b_ctx, self.dry_run)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
