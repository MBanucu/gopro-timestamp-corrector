# Developer documentation

## Architecture

```
   ┌──────────┐
   │  Planner  │  Plan-step options (which corrections to apply)
   │  plan.py  │  Planner, CorrectionPlan, PlanBuilder, Instruction
   └────┬──────┘
        │
   ┌────▼──────┐
   │  preview  │  Calculator — pure computation on in-memory data
   │  resolve  │  target_time(), gps_delta(), weighted_median_delta()
   └────┬──────┘
        │ plan (list of FilePreview / WriteJob)
    ┌────▼──────┐
    │  writer   │  Pure I/O — dispatches WriteJobs to session + btime
    │           │  (per-file exiftool writes via persistent process)
    └───────────┘
```

- **Planner** (`plan.py`): holds plan-step options (which corrections to
  apply, btime chain, dry-run, force) via the :class:`Planner` dataclass.
  :class:`CorrectionPlan` manages per-set strategies and the preview
  computation.  :class:`PlanBuilder` transforms these into a list of
  :class:`Instruction` objects that the Run step executes sequentially.
- **Calculator** (`resolve.py` + `preview.py`): pure math, no file I/O,
  no `media` import. `resolve` has `target_time()`, `gps_delta()` and
  `weighted_median_delta()`.
- **Strategies** (`strategies/`): btime method implementations as
  `BtimeStrategy` subclasses, each declaring its tool dependencies
  via `required_tools()` and proving its own viability via
  `check_capabilities()`. The facade in `btime.py` selects them
  transparently. Additionally, `strategies/mount.py` provides
  `ImageMountStrategy` and `AlreadyMountedStrategy` for loop device
  setup, and `strategies/mtime.py` provides `OsUtimeMtimeStrategy`,
  `ExfatRawMtimeStrategy`, and `SkipMtimeStrategy` for filesystem
  modification time writes.
- **Writer** (`writer.py`): receives a pre-computed list of `WriteJob` objects,
  dispatches to `ExifToolSession.write_embedded` and `btime.fix_file`. No calculator import.
- **Orchestrator** (`correct_timestamps.py` / `gui/app.py`): reads files via
  `analysis.analyze()`, calls the calculator to build a plan, passes the
  same plan to the writer. No recalculation on apply.
  The GUI uses a **sidebar/stepper hybrid** layout (`gui/sidebar.py` +
  `gui/steps/`) that guides the user through four sequential steps:
  directory selection, review & calibration, plan options, and execution. A
  history viewer (`gui/history_viewer.py`) provides a side-by-side
  JSON diff of before/after exiftool data for past correction runs.

### Module overview

| Module | Responsibility |
|---|---|
| `plan.py` | Correction plan (Planner, CorrectionPlan, PlanBuilder, Instruction) |
| `resolve.py` | Pure math helpers (target_time, gps_delta, median) |
| `preview.py` | Calculator — computes the correction plan |
| `writer.py` | Pure I/O dispatcher (takes WriteJob list) |
| `exiftool_session.py` | Persistent exiftool wrapper via PyExifTool (stay_open) |
| `media.py` | Filesystem helpers (collect, read_mtime, write_mtime) |
| `btime.py` | Birth‑time facade (delegates to strategies/) |
| `env_check.py` | System environment checker (tools, btime, strategies) |
| `probe.py` | Capability probes (stat/statx/utime/block device) |
| `history.py` | Modification history logger (before/after exiftool JSON) |
| `loop_device.py` | Loop device setup/teardown convenience |
| `strategies/base.py` | BtimeStrategy abstract base |
| `strategies/exfat_raw.py` | Raw exFAT block manipulation (btime + mtime) |
| `strategies/exfat_raw_read.py` | Raw exFAT btime readback (internal) |
| `strategies/debugfs.py` | debugfs (ext4 inode crtime) |
| `strategies/fuse.py` | FUSE + faketime remount |
| `strategies/clock.py` | System clock manipulation |
| `strategies/mount.py` | ImageMountStrategy + AlreadyMountedStrategy |
| `strategies/mtime.py` | OsUtimeMtimeStrategy + ExfatRawMtimeStrategy + SkipMtimeStrategy |

## Tests

```bash
# Via the Nix derivation (parallel, 4 workers, includes coverage):
nix run .#test                     # full suite
nix run .#test -- test_datepicker  # single module (omit test. prefix)
nix run .#test -- test_analysis test_gps  # multiple specific modules
# then:
COV=$(nix eval .#packages.x86_64-linux.test.outPath --raw)
$COV/bin/coverage-report
```

### Test structure

| Area | Tests | File |
|---|---|---|
| Analysis | 8 unit | `test_analysis.py` |
| Preview / resolve | 11 unit | `test_preview.py` |
| GPS parsing | 3 unit | `test_gps.py` |
| DST fold | 6 unit | `test_dst_fold.py` |
| Autocomplete | 13 unit | `test_autocomplete.py` |
| Common prefix | 4 unit | `test_common_prefix.py` |
| Proposals | 9 unit | `test_proposals.py` |
| Calibration editor | 9 GUI | `test_editor.py` |
| Calendar widget | 16 GUI | `test_datepicker.py` |
| File table widget | 15 GUI | `test_file_table.py` |
| Auto calibration (mock) | 4 unit | `test_calibration_panel.py` |
| CLI integration | 1 integration | `test_img.py` |
| Strategy manifest | 6 integration | `test_strategy.py` |
| Btime / strategies | 22 unit | `test_btime.py` |
| Btime (FUSE+faketime) | 4 integration | `test_fuse_faketime.py` |
| Btime (exFAT raw) | 3 integration | `test_exfat_raw_btime.py` |
| Modification history | 7 unit | `test_history.py` |
| GUI structure smoke | 20 smoke | `test_gui_structure.py` |
| Full pipeline (server) | 1 integration | `test_full_auto_integration.py` |
| Raw debug tests | 7 debug | `test_debug_raw_btime.py` |
| Cluster coherence | 1 integration | `test_cluster_coherence.py` |
| Ubuntu compatibility | 6 unit | `test_ubuntu_compat.py` |
| Unit tests | 28 unit | `test_unit.py` |

### CI workflows

Single `ci` workflow with a job matrix (`debug`, `unit`, `cluster`, `full`):

| Scope | What it runs | ~Duration |
|---|---|---|
| `debug` | `test_debug_raw_btime` (7 tests) | 30s |
| `unit` | `test.test_unit` (28 tests) | 5s |
| `cluster` | `test_cluster_coherence` | 45s |
| `full` | exFAT raw + GUI + full pipeline integration | ~120s |

## Project structure

```
├── flake.nix               # Nix flake (dev shell + apps)
├── README.md
├── AGENTS.md               # Agent instructions (CI workflows, kernel details, conventions)
├── .github/workflows/
│   ├── debug-btime.yml       # Full debug + GUI + timezone CI
│   ├── debug-raw-btime.yml   # Fast cycle: only debug tests
│   └── cluster-coherence.yml # Cluster write coherence diagnostic
├── src/
│   ├── analysis.py
│   ├── btime.py
│   ├── strategies/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── exfat_raw.py
│   │   ├── exfat_raw_read.py
│   │   ├── debugfs.py
│   │   ├── fuse.py
│   │   ├── clock.py
│   │   ├── mount.py
│   │   └── mtime.py
│   ├── calibration.py
│   ├── correct_timestamps.py
│   ├── dst.py
│   ├── env_check.py
│   ├── probe.py
│   ├── exiftool_session.py
│   ├── history.py
│   ├── media.py
│   ├── options.py
│   ├── plan.py
│   ├── preview.py
│   ├── resolve.py
│   ├── scanner.py
│   ├── writer.py
│   ├── loop_device.py
│   ├── gui/
│   │   ├── __init__.py
│   │   ├── app.py
│   │   ├── sidebar.py
│   │   ├── history_viewer.py
│   │   ├── steps/
│   │   │   ├── __init__.py
│   │   │   ├── directory.py
│   │   │   ├── review.py
│   │   │   ├── plan.py
│   │   │   └── run.py
│   │   ├── file_table.py
│   │   ├── editor.py
│   │   ├── cal_file.py
│   │   ├── calibration_panel.py
│   │   ├── tz_info.py
│   │   ├── time_selector.py
│   │   ├── tzcombobox.py
│   │   └── datepicker.py
├── test/
│   ├── sdcard.img.gz
│   ├── sdcard.img (gitignored)
│   ├── shared.py
│   ├── perf_decompress.py
│   ├── run_parallel.py
│   ├── debug_exfat.py
│   ├── test_exfat_raw_btime.py
│   ├── test_debug_raw_btime.py
│   ├── test_fuse_faketime.py
│   ├── test_history.py
│   ├── test_cluster_coherence.py
│   ├── test_ubuntu_compat.py
│   ├── test_unit.py
│   ├── test_analysis.py
│   ├── test_preview.py
│   ├── test_file_table.py
│   ├── test_strategy.py
│   ├── test_img.py
│   ├── test_btime.py
│   ├── test_btime_gui_correction.py
│   ├── test_calibration_panel.py
│   ├── test_editor.py
│   ├── test_gps.py
│   ├── test_gui_structure.py
│   ├── test_dst_fold.py
│   ├── test_datepicker.py
│   ├── test_autocomplete.py
│   ├── test_common_prefix.py
│   └── test_proposals.py
└── docs/
    ├── btime-strategies/
    │   └── README.md
    ├── exfat-raw-implementation.md
    ├── developer.md
    └── SPARSE_EXFAT_REPORT.md
```
