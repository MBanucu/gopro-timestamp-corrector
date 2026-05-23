# GoPro Timestamp Corrector

![GUI screenshot](docs/images/Screenshot%20From%202026-05-18%2023-21-46.png)

Correct the creation timestamps of GoPro videos and thumbnails when the
camera's clock was wrong (e.g. after a dead battery reset or timezone
misconfiguration).

## Why

- **Battery Drain:** A GoPro with a drained battery resets its clock to **January 1, 2016**.
- **Quik App Sync:** Synchronizing the camera with the GoPro Quik app (e.g. on Android) often sets the GoPro's internal clock to the phone's **local time**, but the GoPro interprets it as **UTC**. Since the GoPro lacks timezone settings, all recorded metadata ends up with a fixed offset equal to your local timezone's UTC offset.
- **QuickTime:CreationDate timezone ambiguity:** GoPro firmware stores
  `CreationDate` as a string (unlike the integer-based `CreateDate`/
  `TrackCreateDate` etc.). Omitting the timezone causes exiftool to
  interpret it as local time on readback, shifting the value by the UTC
  offset even when the actual time is correct.

Files recorded in these states get wrong timestamps — both in embedded metadata
and on the filesystem. This tool corrects them in one pass.

All observations above were made on a **GoPro HERO10 Black** running
firmware **v1.62** (released May 23, 2024 — the only listed change is
support for the Waterproof Shutter Remote).

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
- **Auto‑detect GoPro devices** — scans mounted devices for GoPro media
  directories on startup, with recently used paths persisted in a dropdown
- **GUI** with searchable timezone picker, calendar date picker, live
  delta preview, one-click GPS calibration, and an interactive file analysis table
- Corrects **embedded metadata** via `exiftool`:
  - `QuickTime:CreateDate`, `TrackCreateDate`, `MediaCreateDate`, … (MP4/LRV)
  - `EXIF:DateTimeOriginal` and related tags (THM)
- Corrects **filesystem timestamps** (mtime/atime)
- Corrects **birth time** (btime) via **strategy classes** (`src/strategies/`):
  - `debugfs` on ext4 (Linux)
  - raw exFAT block manipulation (primary, for existing files)
  - `mount.exfat-fuse` + `faketime` on exFAT (fallback, new files only)
  - direct system‑clock manipulation (last resort)
  - Each strategy auto‑detects missing tools (sudo, debugfs, faketime, etc.)
    via `check_capabilities()` — methods that can't possibly work are
    hidden from the GUI and skipped by the CLI instead of failing at runtime
- **Daylight‑saving detection** — warns about ambiguous hours (fall‑back /
  spring‑forward) and shows fold selectors
- **Idempotent** — a manifest file prevents double‑correction on re‑runs
- **Modification history** — every correction run records full exiftool JSON
  (before/after) in `.timestamp_correction_history/` for audit and rollback
- **QuickTime:CreationDate timezone fix** — the tool writes
  `QuickTime:CreationDate` with an explicit `+00:00` UTC offset.
  GoPro firmware stores this tag as a string (unlike the integer-based
  `CreateDate`/`TrackCreateDate` etc.), and omitting the timezone causes
  exiftool to interpret it as local time on readback, shifting the displayed
  value by the UTC offset.
- **ISO 8601** date format throughout the GUI with millisecond precision
- **Common‑prefix autocomplete** for timezone entry with **Tab‑to‑accept**
- **All internal times are UTC-aware** (`tzinfo=timezone.utc`) — timezone
  conversion happens only at the display layer, via `zoneinfo`

## Requirements

- [Nix](https://nixos.org) with flakes enabled (recommended), **or**
- Python 3.10+, `exiftool`, `pyexiftool` (PyExifTool on PyPI), and optionally `e2fsprogs`, `exfat`, `libfaketime`

## Environment check

Before running, verify all required tools are available:

```bash
# Via the CLI
nix run . -- --check

# Or standalone
gopro-check-env
```

Example output:

```
Platform:         linux
Python:           3.13.12
Tkinter:          ✓
exiftool:         /nix/store/…/bin/exiftool
Sudo (no-pass):   ✓

Strategies:
  ✓ gps
  ✓ manual
  ✓ skip

Btime methods:
  ✓ exFAT raw block               FS: exfat_raw, fuse, clock
      ✓ dd (coreutils)
      ✓ findmnt (util-linux)
      ✓ sudo
      ✓ sync
      ✓ mount (util-linux)
  ✓ debugfs (ext4)                FS: debugfs, clock
      ✓ debugfs (e2fsprogs)
      ✓ sudo
      ✓ sync
  ✗ FUSE + faketime (exFAT)       FS: exfat_raw, fuse, clock
      ✗ faketime (libfaketime)
      ✗ mount.exfat-fuse (exfat)
  ✓ System clock                  FS: clock
      ✓ timedatectl (systemd)
      ✓ date (coreutils)
      ✓ sudo
```

Each btime method shows its tool dependencies, compatible filesystems,
and whether all deps are met.  Missing tools are marked with ✗.

The exFAT probe additionally checks block‑device capabilities:
`dd iflag=nocache`, `blockdev --flushbufs`, and `os.utime()` on an exFAT
filesystem.  See `src/probe.py` for the full list of probes.

## Quick start

```bash
# Clone and enter
git clone https://github.com/MBanucu/gopro-timestamp-corrector.git
cd gopro-timestamp-corrector

# Run the GUI (all dependencies from Nix)
nix run .#gui

# Run the CLI
nix run . -- /path/to/DCIM/100GOPRO --dry-run

# Check your environment (available tools, btime methods, etc.)
nix run . -- --check
gopro-check-env                    # standalone env check
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
| `--check` | Check system environment and exit — reports available tools, btime methods, and strategies |
| `--dry-run` | Preview changes without writing |
| `--gps` | Determine delta using the first file with GPS data |
| `--timezone` | Target timezone for GPS correction (e.g. `Europe/Berlin`) |
| `--strategy-manifest` | JSON file with per-set strategies (`gps`, `manual`, `skip`) |
| `--fix-btime` | Fix filesystem birth time (auto‑detects ext4 vs exFAT; tries debugfs, exfat_raw, fuse, clock in order) |
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

![GUI screenshot](docs/images/Screenshot%20From%202026-05-18%2023-21-46.png)

```bash
nix run .#gui
```

The GUI provides a complete workflow:

### 1. Select Directory

Select a directory containing GoPro files. The directory combobox
auto‑detects mounted GoPro devices and shows recently used paths.
Click **Analyze** to scan and group files by recording set
(e.g. `GX010063.MP4` + `GL010063.LRV` + `GX010063.THM`).

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

### 3. Plan

Check which corrections to apply:

| Option | Description |
|---|---|
| EXIF / QuickTime metadata | Correct embedded timestamps via `exiftool` |
| Filesystem mtime | Correct modification time via `os.utime` |
| Filesystem btime | Correct birth/creation time (priority‑ordered fallback list) |
| Dry run | Preview changes without writing |
| Force | Ignore the manifest and re‑process all files |

The btime correction uses a **priority‑ordered fallback list**:

- Each method is tried in order; the first one whose setup succeeds is used for all files.
- Add/remove/reorder methods with the ▲▼+✕ buttons.
- When the target directory is known, only **viable methods** for its
  filesystem are shown (e.g. `exfat_raw` + `fuse` + `clock` on exFAT,
  `debugfs` + `clock` on ext4).  Viability considers both filesystem
  compatibility **and** system capabilities — a method whose tools are
  missing (e.g. no `faketime` for FUSE) is hidden automatically.
  An unknown or undetected filesystem defaults to `clock` only.
- The list starts **disabled** — check the *Filesystem birth time (btime)*
  checkbox to enable it.

### 4. Apply (Run)

The Run step displays an **execution plan** as a list of instructions:

| Step | Description |
|---|---|
| Build | Collect file jobs from strategy decisions |
| Capture before | Save exiftool JSON + btime snapshot |
| Write embedded | EXIF / QuickTime metadata correction |
| Write mtime | Filesystem modification time |
| Write btime | Filesystem birth time (method chain) |
| Capture after | Save exiftool JSON + btime snapshot |
| Report | Finalize run history |

Each instruction shows a live status icon during execution
(▶ running, ✓ done, ✗ failed, – skipped). The shared output log
at the bottom displays detailed progress messages.

A single **Apply** button executes all enabled instructions in order.
Dry-run mode shows what would be done without writing.

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

| Filesystem | Method | Sudo | Dependencies checked | Notes |
|---|---|---|---|---|
| ext4 | `debugfs` | required | `debugfs`, `sync` | Sets inode crtime directly |
| exFAT | raw block (`exfat_raw`) | required | `dd`, `findmnt`, `sync`, `mount`, `umount` | Direct block device manipulation; works on existing files (recommended) |
| exFAT | FUSE + `faketime` | for mount | `faketime`, `mount.exfat-fuse`, `findmnt` | Unmounts kernel driver, remounts FUSE exFAT under faked time; new files only |
| any | system clock | required | `timedatectl`, `date` | Temporarily sets `CLOCK_REALTIME` (disruptive) |

When `--fix-btime` is used, the tool auto‑detects the filesystem and picks
the best method. On exFAT the **raw block** method (`exfat_raw`) is the
default; without `sudo` it falls back to FUSE+faketime, and finally to
the clock method.

**Capability‑based filtering** — before trying any method, the tool
checks whether its external dependencies are available on the current
system (tool binaries, passwordless sudo). Methods whose tools are
missing are skipped with a clear warning instead of failing at runtime.
The same information powers the GUI: only strategies whose dependencies
are actually met appear in the btime method list.

**In the GUI**, btime uses a priority‑ordered fallback chain.  Users
add/remove/reorder methods (e.g. `exfat_raw` → `debugfs` → `clock`)
and the system tries each in turn until one succeeds.  When the target
directory is known, incompatible methods (e.g. `debugfs` on exFAT) are
hidden automatically.

See [`docs/exfat-raw-implementation.md`](docs/exfat-raw-implementation.md)
for a detailed report on the exFAT filesystem internals, the raw block
implementation, and how it was tested.

For architecture, test structure, CI workflows, and the full project tree,
see [`docs/developer.md`](docs/developer.md).
