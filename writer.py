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


@dataclass
class WriteJob:
    path: Path
    target_embedded: datetime | None
    target_mtime: datetime | None


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
        fix_btime: str = 'off',
        delta: timedelta | None = None,
        dry_run: bool = False,
    ):
        self.target_dir = target_dir
        self.dry_run = dry_run
        self._b_method: str | None = None
        self._b_ctx: dict = {}
        self._delta = delta

        if fix_btime != 'off':
            fs = btime.detect_fs(target_dir)
            self._b_method = btime.resolve_method(fix_btime, fs)
            if self._b_method and btime.needs_processing_before(self._b_method):
                self._b_ctx = btime.setup(self._b_method, target_dir, delta or timedelta(), dry_run) or {}
                if not self._b_ctx and self._b_method == 'fuse':
                    self._b_method = 'clock'
                    self._b_ctx = btime.setup(self._b_method, target_dir, delta or timedelta(), dry_run) or {}
            if self._b_method == 'clock':
                self._b_ctx = btime.setup(self._b_method, target_dir, delta or timedelta(), dry_run) or {}

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

    def write_all(self, jobs: list[WriteJob]) -> WriteSummary:
        """Write multiple jobs. Returns summary."""
        summary = WriteSummary()
        for job in jobs:
            ok = self.write(job)
            if ok:
                summary.written += 1
            else:
                (summary.errors or []).append(str(job.path))
        return summary

    def close(self):
        """Tear down btime if needed."""
        if self._b_method and (btime.needs_processing_before(self._b_method) or self._b_method == 'clock'):
            btime.teardown(self._b_method, self._b_ctx, self.dry_run)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
