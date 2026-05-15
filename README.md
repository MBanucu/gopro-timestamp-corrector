# GoPro Timestamp Corrector

![GUI screenshot](docs/images/Screenshot%20From%202026-05-14%2021-37-28.png)

Correct the creation timestamps of GoPro videos and thumbnails when the
camera's clock was wrong (e.g. after a dead battery reset).

## Why

A GoPro with a drained battery resets its clock to **January 1, 2016**.
Files recorded afterwards get wrong timestamps — both in embedded metadata
and on the filesystem. This tool corrects them in one pass.

## Features

- **Per-file-set strategy** — analyze all files grouped by recording, then
  decide per-set: use GPS time, a manual calibration delta, or skip entirely
- **GPS strategy** — automatically determine clock offset from embedded GPS data,
  either globally (CLI) or per recording (GUI)
- **Live preview** — see every file's current times (filesystem, EXIF, GPS)
  and what they will become, with per-entry DST (CET/CEST) computed via
  `zoneinfo` from the recording date
- **CLI** for batch-processing entire SD cards
- **GUI** with searchable timezone picker, calendar date picker, live
  delta preview, one-click GPS calibration, and an interactive file analysis table
- Corrects **embedded metadata** via `exiftool`:
  - `QuickTime:CreateDate`, `TrackCreateDate`, `MediaCreateDate`, … (MP4/LRV)
  - `EXIF:DateTimeOriginal` and related tags (THM)
- Corrects **filesystem timestamps** (mtime/atime)
- Corrects **birth time** (btime) via:
  - `debugfs` on ext4 (Linux)
  - `mount.exfat-fuse` + `faketime` on exFAT
  - direct system‑clock manipulation (fallback)
- **Daylight‑saving detection** — warns about ambiguous hours (fall‑back /
  spring‑forward) and shows fold selectors
- **Idempotent** — a manifest file prevents double‑correction on re‑runs
- **ISO 8601** date format throughout the GUI
- **Common‑prefix autocomplete** for timezone entry with **Tab‑to‑accept**
- **All internal times are UTC** — timezone conversion happens only at the
  display layer, via `zoneinfo`. No DST ambiguity in calculations.

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

# Specify a custom calibration file
nix run . -- --translation /path/to/calibration.json /path/to/files
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
| `--translation` | Path to a calibration `.json` or `.txt` file |

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
- **`manual`**: apply the global delta (from `--gps` or `--translation`)
- **`skip`**: leave the set unmodified

Sets not listed in the manifest default to `manual`.

## GUI

![GUI screenshot](docs/images/Screenshot%20From%202026-05-14%2021-37-28.png)

```bash
nix run .#gui
```

The GUI provides a complete workflow:

### 1. Calibration

Set the reference times in the **Actual** and **GoPro** editors. Times are
entered in the chosen IANA timezone; the delta updates live.

- Type or pick a date from the calendar popup
- Search and autocomplete for IANA timezone IDs
- DST warnings appear at transition hours with fold (CET/CEST) radio buttons
- Click **Extract from GPS…** to auto-fill from the first file with GPS data

### 2. Analysis

Click **Analyze** to scan the directory. Files are grouped by recording set
(e.g. `GX010063.MP4` + `GL010063.LRV` + `GX010063.THM`).

The table shows every file with its current times:

| Column | Timezone | Source |
|---|---|---|
| FS mtime | Current system TZ (e.g. CEST) | filesystem modification time |
| EXIF time | Per-entry DST via zoneinfo (CET/CEST) | embedded metadata, raw UTC |
| GPS time | UTC | GPS satellite data |
| Target | Per-entry DST via zoneinfo (CET/CEST) | result of correction |

An info line below the table shows the detected IANA ID and the two possible
DST abbreviations (e.g. `CET (Jan) / CEST (Jul)`).

### 3. Strategy selection

Right-click any set row to choose its correction strategy:

- **Use GPS time** — compute the delta from GPS data within that set
- **Use Manual calibration** — apply the global calibration delta
- **Skip** — leave files untouched

Sets with GPS data default to `gps`; sets without default to `manual`.
The **Target** column updates live with every strategy change.

### 4. Apply

Click **Apply** to write the corrected times. The plan is computed once and
reused — no recalculation happens on apply. Dry-run mode shows what would
be done without writing.

## Calibration file

### JSON format (recommended)

```json
{
  "version": 1,
  "description": "GoPro time calibration reference",
  "actual": {
    "date": "2026-04-25",
    "date_format": "YYYY-MM-DD",
    "time": "14:14",
    "time_format": "HH:MM",
    "timezone": "Europe/Berlin"
  },
  "gopro": {
    "date": "2016-01-04",
    "date_format": "YYYY-MM-DD",
    "time": "00:43",
    "time_format": "HH:MM",
    "timezone": "CET"
  }
}
```

### Plain text format (legacy)

```
## actual local time

date: 2026-04-25
format: year-month-day

time: 14:14
format: hour:minute

## GoPro local time (CET)

time: 00:43
format: hour:minute

date: 01/04/16
format: month/day/year
```

## Daylight saving and DST-correct display

All internal times are **naive UTC**. Embedded metadata is read without
`QuickTimeUTC` conversion (raw stored value, which modern GoPros store as
UTC). GPS times are inherently UTC. All arithmetic (deltas, target
computation) happens in UTC space — **no DST ambiguity in calculations**.

Display conversion happens only at the GUI layer: `zoneinfo.ZoneInfo(iana_id)`
converts UTC wall-clock values to the correct local time for each entry's
date. A January video shows `CET`, a July video shows `CEST` — correctly,
without pinning one DST to the whole column.

The filesystem mtime column uses the current system timezone (since mtime is
a local timestamp), annotated with the current DST abbreviation.

## Birth time (btime) support

| Filesystem | Method | Sudo | Notes |
|---|---|---|---|
| ext4 | `debugfs` | required | Sets inode crtime directly |
| exFAT | FUSE + `faketime` | for mount | Mounts FUSE exFAT driver under faked time |
| any | system clock | required | Temporarily sets `CLOCK_REALTIME` (disruptive) |

When `--fix-btime` is used, the tool auto‑detects the filesystem and picks
the best method. On exFAT without FUSE tools it falls back gracefully to the
clock method.

## Architecture

```
  ┌──────────┐
  │  media   │  Low-level file I/O (exiftool, os.utime)
  │ btime    │
  └────┬─────┘
       │ reads / writes
  ┌────▼─────┐
  │ analysis │  Collects files, reads all metadata into FileInfo objects
  └────┬─────┘
       │
  ┌────▼──────┐
  │  preview  │  Calculator — pure computation on in-memory data
  │  resolve  │  target_time(), gps_delta() — stdlib only, no I/O
  └────┬──────┘
       │ plan (list of FilePreview / WriteJob)
  ┌────▼──────┐
  │  writer   │  Pure I/O — dispatches WriteJobs to media + btime
  └───────────┘
```

- **Calculator** (`resolve.py` + `preview.py`): pure math, no file I/O,
  no `media` import. `resolve` has only `target_time()` and `gps_delta()`.
- **Writer** (`writer.py`): receives a pre-computed list of `WriteJob` objects,
  dispatches to `media.write_*` and `btime.fix_file`. No calculator import.
- **Orchestrator** (`correct_timestamps.py` / `gui.py`): reads files via
  `analysis.analyze()`, calls the calculator to build a plan, passes the
  same plan to the writer. No recalculation on apply.

## Tests

```bash
python3 -m unittest discover test -v
```

Or via the Nix derivation (includes coverage):

```bash
nix run .#test
# then:
COV=$(nix eval .#packages.x86_64-linux.test.outPath --raw)
$COV/bin/coverage-report
```

## Project structure

```
├── correct_timestamps.py   # CLI orchestrator
├── gui.py                  # Tkinter GUI
├── src/
│   ├── analysis.py         # File scanning, grouping, metadata extraction
│   ├── preview.py          # Calculator — computes the correction plan
│   ├── resolve.py          # Pure math helpers (target_time, gps_delta)
│   ├── writer.py           # Pure I/O dispatcher (takes WriteJob list)
│   ├── media.py            # EXIF/QuickTime read/write via exiftool
│   ├── calibration.py      # JSON calibration load/save/parse
│   ├── translate.py        # Plain‑text calibration parser
│   ├── btime.py            # Birth‑time fixing methods
│   ├── dst.py              # DST ambiguity detection
│   ├── correct_timestamps.py  # CLI orchestrator
│   ├── gui/
│   │   ├── __init__.py
│   │   ├── app.py              # Tkinter GUI application
│   │   ├── file_table.py       # FileSetTable widget
│   │   ├── editor.py           # Calibration editor widget
│   │   ├── cal_file.py         # Calibration file management bar
│   │   ├── tz_info.py          # IANA timezone parser + TzInfoPanel
│   │   ├── tzcombobox.py       # FilteringCombobox widget
│   │   └── datepicker.py       # Calendar popup widget
├── flake.nix               # Nix flake (dev shell + apps)
├── test/
│   ├── sdcard.img     # Sparse exFAT disk image (12 real GoPro files)
│   ├── SPARSE_EXFAT_REPORT.md
│   ├── perf_decompress.py  # Benchmark decompress + mount pipeline
│   ├── test_analysis.py    # Analysis module tests (8)
│   ├── test_preview.py     # Preview/calculator tests (11)
│   ├── test_file_table.py  # GUI table widget tests (12)
│   ├── test_strategy.py    # Strategy manifest integration tests (6)
│   ├── test_img.py         # End-to-end CLI integration test
│   ├── test_btime.py       # Birth-time fixing method tests
│   ├── test_gps.py         # GPS time parsing tests
│   ├── test_dst_fold.py    # DST detection tests
│   ├── test_datepicker.py  # Calendar widget tests
│   ├── test_autocomplete.py
│   ├── test_common_prefix.py
│   └── test_proposals.py
└── .gitignore
```
