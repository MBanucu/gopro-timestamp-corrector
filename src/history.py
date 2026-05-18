"""Modification history logger.

Captures full exiftool JSON before and after each modification run,
along with run metadata (timestamp, strategies, delta). Each run
creates an append-only timestamped entry — no data is ever overwritten.

Run directory structure::

    <target_dir>/.timestamp_correction_history/
    ├── 20260517T163000Z/
    │   ├── run.json          # run metadata + summary
    │   ├── before.json       # full exiftool -json output (before)
    │   └── after.json        # full exiftool -json output (after)
    └── 20260518T091500Z/
        ├── run.json
        ├── before.json
        └── after.json
"""
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HISTORY_DIR_NAME = '.timestamp_correction_history'


def _dump_batch(filepaths: list[Path]) -> str | None:
    """Full exiftool JSON array for a list of files."""
    if not filepaths:
        return None
    cmd = ['exiftool', '-json', '-G', '-a', '--short'] + [str(p) for p in filepaths]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout
    return None


def _capture_btimes(filepaths: list[Path]) -> dict[str, str | None]:
    """Capture birth (creation) times via ``stat -c %w``."""
    btimes = {}
    for fp in filepaths:
        try:
            r = subprocess.run(
                ['stat', '-c', '%w', str(fp)],
                capture_output=True, text=True, timeout=5)
            val = r.stdout.strip() if r.returncode == 0 else None
            if val in (None, '', '-'):
                val = None
            btimes[str(fp)] = val
        except Exception:
            btimes[str(fp)] = None
    return btimes


def begin_run(target_dir: Path, metadata: dict[str, Any]) -> Path:
    """Create a new history run directory and write run metadata.

    *metadata* is a dict serialised into ``run.json``.  Typical keys::

        {
            "timestamp": "2026-05-17T16:30:00Z",
            "fix_btime": "exfat_raw",
            "global_delta": "-1 day, 22:00:00.600000",
            "strategy": "gps",               # overall strategy description
            "sets": {                         # per-set strategy / delta
                "010063": {"strategy": "gps"},
                "010064": {"strategy": "manual", "delta": "-1 day, 22:00:00"}
            }
        }

    Returns the run directory path.
    """
    ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    metadata.setdefault('timestamp', ts)
    run_dir = target_dir / HISTORY_DIR_NAME / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / 'run.json').write_text(json.dumps(metadata, indent=2, default=str))
    return run_dir


def capture_before(run_dir: Path, filepaths: list[Path]):
    """Save full exiftool JSON *before* modification as ``before.json``."""
    raw = _dump_batch(filepaths)
    if raw:
        (run_dir / 'before.json').write_text(raw)
    btimes = _capture_btimes(filepaths)
    if any(v is not None for v in btimes.values()):
        (run_dir / 'btimes_before.json').write_text(
            json.dumps(btimes, indent=2))


def capture_after(run_dir: Path, filepaths: list[Path]):
    """Save full exiftool JSON *after* modification as ``after.json``."""
    raw = _dump_batch(filepaths)
    if raw:
        (run_dir / 'after.json').write_text(raw)
    btimes = _capture_btimes(filepaths)
    if any(v is not None for v in btimes.values()):
        (run_dir / 'btimes_after.json').write_text(
            json.dumps(btimes, indent=2))


def finalize_run(run_dir: Path, written: int, skipped: int = 0,
                 errors: list[str] | None = None):
    """Append summary to the run's ``run.json``."""
    meta_path = run_dir / 'run.json'
    meta = json.loads(meta_path.read_text())
    meta['summary'] = {
        'written': written,
        'skipped': skipped,
        'errors': errors or [],
    }
    meta_path.write_text(json.dumps(meta, indent=2, default=str))
