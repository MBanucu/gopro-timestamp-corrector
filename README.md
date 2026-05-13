# GoPro Timestamp Corrector

Correct the creation timestamps of GoPro videos and thumbnails when the
camera's clock was wrong (e.g. after a dead battery reset).

## Why

A GoPro with a drained battery resets its clock to **January 1, 2016**.
Files recorded afterwards get wrong timestamps — both in embedded metadata
and on the filesystem. This tool corrects them in one pass.

## How it works

Place a **calibration file** (`.json` or `.txt`) in the target directory with
a reference measurement:

```json
{
  "actual": { "date": "2026-04-25", "time": "14:14", "timezone": "CEST" },
  "gopro":  { "date": "01/04/16",   "time": "00:43", "timezone": "CET"  }
}
```

The tool computes the clock offset and applies it to every matching file.

## Features

- **CLI** for batch-processing entire SD cards
- **GUI** with searchable timezone picker, calendar date picker, and live
  delta preview
- Corrects **embedded metadata** via `exiftool`:
  - `QuickTime:CreateDate`, `TrackCreateDate`, `MediaCreateDate`, … (MP4/LRV)
  - `EXIF:DateTimeOriginal` and related tags (THM)
- Corrects **filesystem timestamps** (mtime/atime)
- Corrects **birth time** (btime) via:
  - `debugfs` on ext4 (Linux)
  - `mount.exfat-fuse` + `faketime` on exFAT
  - direct system‑clock manipulation (fallback)
- **Daylight‑saving detection** — warns about ambiguous hours (fall‑back /
  spring‑forward) and shows fold selectors for precise timezone handling
- **Idempotent** — a manifest file prevents double‑correction on re‑runs
- **Smart recovery** — detects over‑corrected, already‑corrected, and
  original files and handles each correctly
- **ISO 8601** date format throughout the GUI
- **Common‑prefix autocomplete** for timezone entry with **Tab‑to‑accept**

## Requirements

- [Nix](https://nixos.org) with flakes enabled (recommended), **or**
- Python 3.9+, `exiftool`, and optionally `e2fsprogs`, `exfat`, `libfaketime`

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

# Also fix creation time (birth time)
nix run . -- --fix-btime /path/to/files

# Reprocess already-corrected files with UTC timezone handling
nix run . -- --reprocess /path/to/files

# Specify a custom translation file
nix run . -- --translation /path/to/calibration.json /path/to/files
```

### Options

| Flag | Description |
|---|---|
| `--dry-run` | Preview changes without writing |
| `--fix-btime` | Fix filesystem birth time (auto‑detects ext4 vs exFAT) |
| `--reprocess` | Re‑write all files with proper UTC handling |
| `--force` | Ignore the manifest and re‑process all files |
| `--translation` | Path to a calibration `.json` or `.txt` file |

## GUI

```bash
nix run .#gui
```

The GUI provides a clean interface to:

1. **Select the target directory**
2. **Load, save, or auto‑detect** a calibration file (JSON / plain text)
3. **Edit calibration values directly** — ISO date, 24h time, IANA timezone
4. **See the delta** update live as you type
5. **Get DST warnings** when the entered time falls in an ambiguous
   transition period, with fold selectors (CET vs CEST)
6. **Pick dates** from a calendar popup
7. **Run** with Dry run / Reprocess / Force options
8. **View output** in an integrated terminal panel

### Tab navigation (GUI timezone field)

- Type to filter — autocomplete fills the **common prefix** when multiple
  entries match, or the **full entry** when only one match exists
- **Tab** accepts the suggestion and moves to the next field
- A second **Tab** (no selection active) moves focus to the next widget

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
    "timezone": "CEST"
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

The plain text format uses the GoPro's native `MM/DD/YY` date format.
The JSON format uses ISO 8601 (`YYYY-MM-DD`) for both sides.

## Birth time (btime) support

| Filesystem | Method | Sudo | Notes |
|---|---|---|---|
| ext4 | `debugfs` | required | Sets inode crtime directly |
| exFAT | FUSE + `faketime` | for mount | Mounts FUSE exFAT driver under faked time |
| any | system clock | required | Temporarily sets `CLOCK_REALTIME` (disruptive) |

When `--fix-btime` is used, the tool auto‑detects the filesystem and picks
the best method. On exFAT without FUSE tools it falls back gracefully to the
clock method.

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
├── correct_timestamps.py   # CLI entry point
├── gui.py                  # Tkinter GUI
├── tzcombobox.py           # FilteringCombobox widget
├── media.py                # EXIF/QuickTime read/write via exiftool
├── calibration.py          # JSON calibration load/save/parse
├── translate.py            # Plain‑text calibration parser
├── btime.py                # Birth‑time fixing methods
├── dst.py                  # DST ambiguity detection
├── datepicker.py           # Calendar popup widget
├── flake.nix               # Nix flake (dev shell + apps)
├── test/                   # 41 automated tests (unittest)
│   ├── test_autocomplete.py
│   ├── test_common_prefix.py
│   ├── test_proposals.py
│   ├── test_dst_fold.py
│   └── test_datepicker.py
└── .gitignore
```
