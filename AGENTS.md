# AGENTS.md — gopro-timestamp-corrector

## Build & run

```sh
# All deps via Nix flakes; no system Python packages needed.
nix run .#gui          # launch GUI
nix run . -- --help    # CLI
nix run .#test              # full test suite (parallel, coverage)
nix run .#test -- test_datepicker  # single module (omit test. prefix)
nix run .#test -- test_analysis test_gps  # multiple specific modules
nix develop            # dev shell with exiftool, e2fsprogs, etc.
```

## Tests

```sh
# Parallel runner (default: all cores):
PYTHONPATH=src:test python3 test/run_parallel.py -j 4

# Single test module:
PYTHONPATH=src:test python3 -m unittest test.test_datepicker -v

# Serial discovery:
PYTHONPATH=src:test python3 -m unittest discover -s test -v
```

- GUI tests run headlessly via Xvfb (no display required).
- Integration tests (`test_exfat_raw_btime.py`, `test_fuse_faketime.py`, `test_full_auto_integration.py`) need `sudo`/FUSE and are **not** run by `nix run .#test`.
- `test/test_strategy.py` writes temp files, must be run from repo root (`PYTHONPATH=src:test`).
- Large fixture: `test/sdcard.img.gz` (~14 MB, decompressed on first test run).
- No `pyproject.toml` or pre-commit config.

## Architecture

| Layer | Directory | Entrypoint |
|---|---|---|
| CLI orchestrator | `src/` | `correct_timestamps.py` |
| ExifTool session | `src/` | `exiftool_session.py` — persistent `-stay_open` wrapper via PyExifTool |
| Plan / Planner | `src/` | `plan.py` — `Planner`, `CorrectionPlan`, `PlanBuilder`, `Instruction` |
| GUI app | `src/gui/` | `app.py` |
| GUI steps | `src/gui/steps/` | `directory.py`, `review.py`, `plan.py`, `run.py` |
| Tests | `test/` | one file per area |

Key flow: `ExifToolSession()` → `analysis.analyze(session)` → `preview` calculator → `PlanBuilder.build()` (`Instruction` list) → `Writer(session=session)` I/O.

All internal times carry `tzinfo=timezone.utc`; display-layer DST via `zoneinfo`.

## Code conventions

- No `pyproject.toml` — pure stdlib except `pyexiftool` external dep (PyExifTool on PyPI).
- Module granularity is fine (one class per file common for widgets).
- `options.py` is the single source of truth for strategy/btime/format constants.
- `src/gui/time_selector.py` uses `StringVar` (not `IntVar`) for spinbox variables.
- `TimeSelector` conditionally defines `sec_var`/`ms_var` attributes only when `show_seconds=True`.
- Btime uses a **priority‑ordered fallback chain**: `btime.chain_setup()` in `btime.py`
  tries each method in order and returns the first one whose setup succeeds.
  The GUI filters methods per filesystem via `btime.compatible_methods(fs_type)`.
- `_rebuild_listbox()` temporarily switches the listbox to `NORMAL` before calling
  `delete()`/`insert()` — tkinter silently ignores these calls when the widget is `DISABLED`.
- `Writer.__init__` accepts `fix_btime: str | list[str] | tuple[str]` — a list
  produces a fallback chain; a single string is wrapped for backward compatibility.
- `_fix_exfat_raw` in `btime.py` writes **both** creation time AND modification time
  fields in one raw-block access.  Before any raw device read it calls `sync` to
  flush pending kernel writes (e.g. from the embedded exiftool batch).  After the
  write it calls `sync` + `drop_caches`.
- `Writer.close()` remounts the partition after `exfat_raw` corrections to clear the
  exFAT driver's private metadata cache (not invalidated by `drop_caches`).
- When writing embedded metadata, `QuickTime:CreationDate` receives an explicit
  `+00:00` suffix — this tag is stored as a string (unlike the integer-based
  `CreateDate`/`TrackCreateDate`), and omitting the timezone causes exiftool to
  interpret it as local time on readback.
- Manifest file: `.timestamp_correction_log` (idempotency guard).
- History: `.timestamp_correction_history/` with before/after exiftool JSON.
- `Planner` in `plan.py` is the single source of truth for plan-step options
  (which corrections to apply, btime chain, dry-run, force).
  `PlanBuilder.build()` produces a list of `Instruction` objects from the
  `Planner` + `CorrectionPlan`.  `PlanBuilder.execute()` runs them
  sequentially with progress callbacks (`log_fn`, `progress_fn`).
