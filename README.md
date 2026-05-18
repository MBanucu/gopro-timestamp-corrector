# GoPro Timestamp Corrector

![GUI screenshot](docs/images/Screenshot%20From%202026-05-15%2022-41-44.png)

Correct the creation timestamps of GoPro videos and thumbnails when the
camera's clock was wrong (e.g. after a dead battery reset or timezone
misconfiguration).

## Why

- **Battery Drain:** A GoPro with a drained battery resets its clock to **January 1, 2016**.
- **Quik App Sync:** Synchronizing the camera with the GoPro Quik app (e.g. on Android) often sets the GoPro's internal clock to the phone's **local time**, but the GoPro interprets it as **UTC**. Since the GoPro lacks timezone settings, all recorded metadata ends up with a fixed offset equal to your local timezone's UTC offset.

Files recorded in these states get wrong timestamps — both in embedded metadata
and on the filesystem. This tool corrects them in one pass.

## Features

- **Per-file-set strategy** — analyse all files grouped by recording, then
  decide per-set: use GPS time, a manual calibration delta, or skip entirely
- **GPS strategy** — automatically determine clock offset from embedded GPS data,
  either globally (CLI) or per-recording (GUI)
- **Auto calibrate** — aggregate GPS deltas across all files via **weighted
  median**, filtering by `GPSHPositioningError` for accuracy. Falls back to
  single-file extraction when too few fixes are available
- **Direct delta entry** — type the offset directly (`+2h30m15s500ms`,
  `-1d5h`, `2:30`) without setting two separate date fields
- **Live preview** — see every file's current times (filesystem, EXIF, GPS)
  and what they will become, with per-entry DST (CET/CEST) detected from
  the actual IANA transition data
- **CLI** for batch-processing entire SD cards
- **GUI** with searchable timezone picker, calendar date picker, live
  delta preview, one-click GPS calibration, and an interactive file analysis table
- Corrects **embedded metadata** via `exiftool`:
  - `QuickTime:CreateDate`, `TrackCreateDate`, `MediaCreateDate`, … (MP4/LRV)
  - `EXIF:DateTimeOriginal` and related tags (THM)
- Corrects **filesystem timestamps** (mtime/atime)
- Corrects **birth time** (btime) via:
  - `debugfs` on ext4 (Linux)
  - raw exFAT block manipulation (primary, for existing files)
  - `mount.exfat-fuse` + `faketime` on exFAT (fallback, new files only)
  - direct system‑clock manipulation (last resort)
- **Daylight‑saving detection** — warns about ambiguous hours (fall‑back /
  spring‑forward) and shows fold selectors
- **Idempotent** — a manifest file prevents double‑correction on re‑runs
- **Modification history** — every correction run records full exiftool JSON
  (before/after) in `.timestamp_correction_history/` for audit and rollback
- **ISO 8601** date format throughout the GUI with millisecond precision
- **Common‑prefix autocomplete** for timezone entry with **Tab‑to‑accept**
- **All internal times are UTC-aware** (`tzinfo=timezone.utc`) — timezone
  conversion happens only at the display layer, via `zoneinfo`

## Requirements

- [Nix](https://nixos.org) with flakes enabled (recommended), **or**
- Python 3.10+, `exiftool`, and optionally `e2fsprogs`, `exfat`, `libfaketime`

## Quick start

```bash
# Clone and enter
git clone https://github.com/MBanucu/gopro-timestamp-corrector.git
cd gopro-timestamp-corrector

# Run the GUI (all dependencies from Nix)
nix run .#gui

# Run the CLI
nix run . -- /path/to/DCIM/100GOPRO --dry-run
```

## CLI usage

```bash
# Correct all MP4/LRV/THM files in a directory
nix run . -- /run/media/user/SD_CARD/DCIM/100GOPRO

# Preview without making changes
nix run . -- /path/to/files --dry-run

# Use GPS time to determine the correction delta
nix run . -- /path/to/files --gps

# Also fix creation time (birth time)
nix run . -- --fix-btime /path/to/files

# Per-set strategies via a strategy manifest JSON
nix run . -- /path/to/files --strategy-manifest strategies.json --gps
```

### Options

| Flag | Description |
|---|---|
| `--dry-run` | Preview changes without writing |
| `--gps` | Determine delta using the first file with GPS data |
| `--timezone` | Target timezone for GPS correction (e.g. `Europe/Berlin`) |
| `--strategy-manifest` | JSON file with per-set strategies (`gps`, `manual`, `skip`) |
| `--fix-btime` | Fix filesystem birth time (auto‑detects ext4 vs exFAT) |
| `--force` | Ignore the manifest and re‑process all files |

### Strategy manifest

```json
{
  "version": 1,
  "sets": {
    "010063": { "strategy": "gps" },
    "010064": { "strategy": "gps" },
    "010065": { "strategy": "manual" },
    "010066": { "strategy": "skip" }
  }
}
```

Each set is identified by the numeric stem of its filenames (e.g. `010063`
matches `GX010063.MP4`, `GL010063.LRV`, `GX010063.THM`).

- **`gps`**: compute delta from GPS data within that set (ignores the global delta)
- **`manual`**: apply the global delta (from `--gps`)
- **`skip`**: leave the set unmodified

Sets not listed in the manifest default to `manual`.

## GUI

![GUI screenshot](docs/images/Screenshot%20From%202026-05-15%2022-41-44.png)

```bash
nix run .#gui
```

The GUI provides a complete workflow:

### 1. Select Directory

Select a directory containing GoPro files. Click **Analyze** to scan and group
files by recording set (e.g. `GX010063.MP4` + `GL010063.LRV` + `GX010063.THM`).

### 2. Review & Calibrate

Two calendar editors set the reference times in the **Actual** and **GoPro**
editors. Both times are in the same IANA timezone; the delta updates live.

| Field | Description |
|---|---|
| Date | ISO 8601 with calendar popup (blinks red when empty) |
| Time | HH:MM:SS.mmm spinboxes |
| TZ | Searchable autocomplete with Tab‑to‑accept (blinks red/`(UTC)` when empty/invalid) |

The delta offset can also be adjusted directly via dedicated spinboxes for
days, hours, minutes, seconds, and milliseconds, with a ± toggle for the sign.

Two GPS extraction buttons:
- **Single GPS…** — fills editors from the first file with GPS data
- **Auto calibrate** — reads all files, filters by `GPSHPositioningError < 25 m`,
  computes the weighted median delta, sets the delta spinboxes, and populates the
  calendar editors from the representative file

Below the calibration controls, the file table shows every file with its current
times and deltas:

| Column | Timezone | Source |
|---|---|---|
| FS mtime | Current system TZ (e.g. CEST) | filesystem modification time |
| EXIF time | Per-entry DST via zoneinfo (CET/CEST) | embedded metadata (UTC) |
| GPS time | UTC | GPS satellite data |
| Δ GPS−EXIF | — | `gps_time - embedded_time` (raw per-file) |
| Δ applied | — | `target_time - current_time` (depends on strategy) |
| Target | Per-entry DST via zoneinfo (CET/CEST) | result of correction |

A foldable **TzInfoPanel** below the table shows the timezone transition
history from the IANA database (toggle ▶/▼).

Right-click any set row to choose its correction strategy:

- **Use GPS time** — compute the delta from GPS data within that set
- **Use Manual calibration** — apply the global calibration delta
- **Skip** — leave files untouched

Three **All *** buttons set the strategy for every set at once (All manual /
All GPS / All skip). The **Target** and **Δ applied** columns update live.

### 3. Apply (Run)

Four buttons write the corrections:

| Button | Writes |
|---|---|
| Apply All | EXIF, mtime, btime (if enabled) |
| Run exiftool | EXIF/QuickTime metadata only |
| Adapt mtime | Filesystem modification time only |
| Adapt btime | Filesystem birth time only (needs FUSE or sudo) |

Dry-run mode shows what would be done without writing. The plan is computed
once and reused — no recalculation on apply.

## Calibration file

### JSON format (recommended)

```json
{
  "version": 1,
  "description": "GoPro time calibration reference",
  "actual": {
    "date": "2026-04-25",
    "date_format": "YYYY-MM-DD",
    "time": "14:14:00.000",
    "time_format": "HH:MM:SS.mmm",
    "timezone": "Europe/Berlin"
  },
  "gopro": {
    "date": "2016-01-04",
    "date_format": "YYYY-MM-DD",
    "time": "00:43:00.000",
    "time_format": "HH:MM:SS.mmm"
  }
}
```

The delta is computed as `actual - gopro`. When both sides share the same
timezone, the result is the camera clock error (timezone offset cancels out).

## Daylight saving and DST-correct display

All internal times carry `tzinfo=timezone.utc`. Embedded metadata is read
without `QuickTimeUTC` conversion (raw stored value, which modern GoPros
store as UTC). GPS times are inherently UTC. All arithmetic (deltas, target
computation) happens in UTC space — **no DST ambiguity in calculations**.

Display conversion happens only at the GUI layer: `zoneinfo.ZoneInfo(iana_id)`
converts UTC wall-clock values to the correct local time for each entry's
date. A January video shows `CET`, a July video shows `CEST` — correctly,
without pinning one DST to the whole column.

The filesystem mtime column uses the current system timezone (since mtime is
a local timestamp), annotated with the current DST abbreviation.

The full IANA transition history is shown in the foldable TzInfoPanel,
loaded directly from the compiled TZif v2+ binary files on disk.

## Birth time (btime) support

| Filesystem | Method | Sudo | Notes |
|---|---|---|---|---|
| ext4 | `debugfs` | required | Sets inode crtime directly |
| exFAT | raw block (`exfat_raw`) | required | Direct block device manipulation; works on existing files (recommended) |
| exFAT | FUSE + `faketime` | for mount | Unmounts kernel driver, remounts FUSE exFAT under faked time; new files only |
| any | system clock | required | Temporarily sets `CLOCK_REALTIME` (disruptive) |

When `--fix-btime` is used, the tool auto‑detects the filesystem and picks
the best method. On exFAT the **raw block** method (`exfat_raw`) is the
default; without `sudo` it falls back to FUSE+faketime, and finally to
the clock method.

See [`docs/exfat-raw-implementation.md`](docs/exfat-raw-implementation.md)
for a detailed report on the exFAT filesystem internals, the raw block
implementation, and how it was tested.

## Architecture

```
  ┌──────────┐
  │  media   │  Low-level file I/O (exiftool, os.utime)
  │ btime    │
  └────┬─────┘
       │ reads / writes
  ┌────▼─────┐
  │ analysis │  Collects files, reads all metadata (batch exiftool JSON)
  └────┬─────┘
       │
  ┌────▼──────┐
  │  preview  │  Calculator — pure computation on in-memory data
  │  resolve  │  target_time(), gps_delta(), weighted_median_delta()
  └────┬──────┘
       │ plan (list of FilePreview / WriteJob)
  ┌────▼──────┐
  │  writer   │  Pure I/O — dispatches WriteJobs to media + btime
  │           │  (batch exiftool JSON import for writes)
  └───────────┘
```

- **Calculator** (`resolve.py` + `preview.py`): pure math, no file I/O,
  no `media` import. `resolve` has `target_time()`, `gps_delta()` and
  `weighted_median_delta()`.
- **Writer** (`writer.py`): receives a pre-computed list of `WriteJob` objects,
  dispatches to `media.write_*` and `btime.fix_file`. No calculator import.
- **Orchestrator** (`correct_timestamps.py` / `gui/app.py`): reads files via
  `analysis.analyze()`, calls the calculator to build a plan, passes the
  same plan to the writer. No recalculation on apply.
  The GUI uses a **sidebar/stepper hybrid** layout (`gui/sidebar.py` +
  `gui/steps/`) that guides the user through three sequential steps:
  directory selection, review & calibration, and execution. A
  history viewer (`gui/history_viewer.py`) provides a side-by-side
  JSON diff of before/after exiftool data for past correction runs.

## Tests

```bash
# Via the Nix derivation (parallel, 4 workers, includes coverage):
nix run .#test
# then:
COV=$(nix eval .#packages.x86_64-linux.test.outPath --raw)
$COV/bin/coverage-report

# Directly (parallel) — requires the Python env from flake.nix:
#   nix develop  # or: nix shell .#pythonEnv
PYTHONPATH=src:test python3 test/run_parallel.py -j 4

# Serial — same prerequisite:
PYTHONPATH=src:test python3 -m unittest discover -s test -v
```

### Test structure

| Area | Tests | File |
|---|---|---|
| Analysis | 8 unit | `test_analysis.py` |
| Preview / resolve | 11 unit | `test_preview.py` |
| GPS parsing | 2 unit | `test_gps.py` |
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
| Btime (unit) | 14 unit | `test_btime.py` |
| Btime (FUSE+faketime) | 4 integration | `test_fuse_faketime.py` |
| Btime (exFAT raw) | 3 integration | `test_exfat_raw_btime.py` |
| Modification history | 7 unit | `test_history.py` |
| GUI structure smoke | 12 smoke | `test_gui_structure.py` |
| Auto calibration (real) | 3 integration | `test_auto_calibrate_integration.py` |
| Full pipeline | 1 integration | `test_full_auto_integration.py` |

## Project structure

```
├── flake.nix               # Nix flake (dev shell + apps)
├── README.md
├── .gitignore
├── src/
│   ├── analysis.py         # File scanning, grouping, metadata extraction
│   ├── preview.py          # Calculator — computes the correction plan
│   ├── resolve.py          # Pure math helpers (target_time, gps_delta, median)
│   ├── writer.py           # Pure I/O dispatcher (takes WriteJob list)
│   ├── media.py            # EXIF/QuickTime read/write via exiftool (batch JSON)
│   ├── calibration.py      # JSON calibration load/save/parse
│   ├── btime.py            # Birth‑time fixing methods (incl. exFAT raw block)
│   ├── history.py          # Modification history logger (before/after exiftool JSON)
│   ├── dst.py              # DST ambiguity detection
│   ├── correct_timestamps.py  # CLI orchestrator
│   ├── gui/
│   │   ├── __init__.py
│   │   ├── app.py                # Tkinter orchestrator (sidebar + step panels)
│   │   ├── sidebar.py            # Step indicator sidebar (①–③ + History)
│   │   ├── history_viewer.py     # History browser + side-by-side diff viewer
│   │   ├── steps/
│   │   │   ├── __init__.py
│   │   │   ├── directory.py      # Step 1: directory selection + analyze
│   │   │   ├── review.py         # Step 2: review table + calibration
│   │   │   └── run.py            # Step 3: options, run buttons, output
│   │   ├── file_table.py         # FileSetTable widget (10 columns, delta support)
│   │   ├── editor.py             # Calibration editor widget (HH:MM:SS.mmm)
│   │   ├── cal_file.py           # Calibration file management bar
│   │   ├── calibration_panel.py  # Calendar editors + delta spinboxes + GPS
│   │   ├── tz_info.py            # IANA TZif parser + TzInfoPanel
│   │   ├── tzcombobox.py         # FilteringCombobox widget
│   │   └── datepicker.py         # Calendar popup widget
├── test/
│   ├── sdcard.img.gz      # Compressed sparse exFAT test image (12 files)
│   ├── sdcard.img           # Gitignored — decompressed on first test run
│   ├── shared.py            # Decompress helper for test images
│   ├── perf_decompress.py   # Benchmark decompress + mount pipeline
│   ├── run_parallel.py      # Parallel test runner
│   ├── SPARSE_EXFAT_REPORT.md
│   ├── debug_exfat.py       # Debug helper: raw hex dump of exFAT dir entries
│   ├── test_exfat_raw_btime.py  # Integration tests for raw exFAT btime
│   ├── test_fuse_faketime.py    # Integration tests for FUSE+faketime btime
│   ├── test_history.py          # Tests for modification history logger
│   ├── test_analysis.py
│   ├── test_preview.py
│   ├── test_file_table.py
│   ├── test_strategy.py
│   ├── test_img.py
│   ├── test_btime.py
│   ├── test_auto_calibrate_integration.py
│   ├── test_full_auto_integration.py
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
    │   └── README.md       # All known btime-on-exFAT strategies
    ├── exfat-raw-implementation.md  # Detailed exFAT impl. report
    └── images/
        └── Screenshot From 2026-05-15 22-41-44.png
```
