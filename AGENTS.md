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

- GUI tests require `tkinter` and a display (skipped otherwise).
- Integration tests (`test_exfat_raw_btime.py`, `test_fuse_faketime.py`, `test_full_auto_integration.py`) need `sudo`/FUSE and are **not** run by `nix run .#test`.
- `test/test_strategy.py` writes temp files, must be run from repo root (`PYTHONPATH=src:test`).
- Large fixture: `test/sdcard.img.gz` (~14 MB, decompressed on first test run).
- No `pyproject.toml` or pre-commit config.

## Architecture

| Layer | Directory | Entrypoint |
|---|---|---|
| CLI orchestrator | `src/` | `correct_timestamps.py` |
| GUI app | `src/gui/` | `app.py` |
| GUI steps | `src/gui/steps/` | `directory.py`, `review.py`, `run.py` |
| Tests | `test/` | one file per area |

Key flow: `analysis.analyze()` → `preview` calculator → `Writer` I/O.

All internal times carry `tzinfo=timezone.utc`; display-layer DST via `zoneinfo`.

## Code conventions

- No `pyproject.toml` — pure stdlib except `exiftool` external dep.
- Module granularity is fine (one class per file common for widgets).
- `options.py` is the single source of truth for strategy/btime/format constants.
- `src/gui/time_selector.py` uses `StringVar` (not `IntVar`) for spinbox variables.
- `TimeSelector` conditionally defines `sec_var`/`ms_var` attributes only when `show_seconds=True`.
- Manifest file: `.timestamp_correction_log` (idempotency guard).
- History: `.timestamp_correction_history/` with before/after exiftool JSON.
