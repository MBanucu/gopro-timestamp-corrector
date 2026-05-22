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

- GUI tests run headlessly via Xvfb (no display required).
- Integration tests (`test_exfat_raw_btime.py`, `test_fuse_faketime.py`, `test_full_auto_integration.py`) need `sudo`/FUSE and are **not** run by `nix run .#test`.
- `test/test_strategy.py` writes temp files, must be run from repo root.
- Large fixture: `test/sdcard.img.gz` (~14 MB, decompressed on first test run).

## Architecture

| Layer | Directory | Entrypoint |
|---|---|---|
| CLI orchestrator | `src/` | `correct_timestamps.py` |
| ExifTool session | `src/` | `exiftool_session.py` — persistent `-stay_open` wrapper via PyExifTool |
| Plan / Planner | `src/` | `plan.py` — `Planner`, `CorrectionPlan`, `PlanBuilder`, `Instruction` |
| GUI app | `src/gui/` | `app.py` |
| GUI steps | `src/gui/steps/` | `directory.py`, `review.py`, `plan.py`, `run.py` |
| Mount strategies | `src/strategies/` | `mount.py` — `ImageMountStrategy`, `AlreadyMountedStrategy` |
| Mtime strategies | `src/strategies/` | `mtime.py` — `OsUtimeMtimeStrategy`, `ExfatRawMtimeStrategy`, `SkipMtimeStrategy` |
| Capability probes | `src/` | `probe.py` — `probe_stat_btime`, `probe_statx_btime`, `probe_exfat_btime`, etc. |
| Env check | `src/` | `env_check.py` — `check_env()`, `format_summary()`, CLI `--check` |
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

## exFAT raw block write & cache coherence

### `_fix_exfat_raw` (exfat_raw.py)

- Writes **both** creation time AND modification time in one raw-block access.
- Calls `sync()` after write to flush pending kernel writes.
- **Does NOT call `drop_caches`** — on older kernels (<6.12), `drop_caches` with
  loop devices over sparse backing files causes `EIO` on subsequent reads.
  `sync` alone is sufficient for the raw block write to persist.

### `Writer.close()` (writer.py)

- **No longer does `umount` + `mount`** — the full remount cycle caused
  `EIO` errors on CI's kernel because the kernel exFAT driver re-validates
  directory entries after remount, and the CRC validation can fail for
  raw-block-modified entries.
- The stale kernel cache is a cosmetic issue: `stat` shows the old mtime,
  but the raw block data is correct. The corrected mtime becomes visible
  after the user unmounts/mounts the SD card normally.

### test image FAT inconsistency

The `sdcard.img` test fixture has an **inconsistent FAT**: the FAT entries
for used clusters (DCIM at cluster 5, 100GOPRO at cluster 9, etc.) are
marked as free (value 0).  This means:

- **Do NOT use `_find_free_cluster()`** on the test image — it will return
  an in-use cluster and corrupt the filesystem.
- Use a known-safe sector offset for dd write tests (e.g., `100000 * 512`).

## Kernel version differences

| Feature | NixOS (kernel 6.12+) | CI Ubuntu (kernel <6.12) |
|---|---|---|
| `os.utime()` on exFAT | ✓ works | ✗ EPERM |
| `stat -c '%W'` on exFAT | ✓ returns btime | ✗ returns 0 |
| `statx STATX_BTIME` on exFAT | ✓ | ✗ returns 0 in value (mask says supported but value is 0) |
| Raw block write + `sync` | ✓ works | ✓ works |
| `drop_caches` after raw write | ✓ safe | ✗ causes EIO on loop reads |
| `dd` write to loop after FS modification | ✓ works | ✗ kernel remounts read-only |
| exFAT kernel UTC offset | ✓ none | ✗ 7200s (UTC+2) applied to `stat` mtime |

### `_parse_dt` timezone handling

`exiftool_session._parse_dt()` now **parses the timezone offset** from exiftool
output strings (e.g., `2026:05:14 14:52:00+09:00`) and converts to UTC, instead
of stripping the offset and stamping the local time as UTC.  The old `_strip_tz()`
function has been removed.

This is critical for the timezone integration test (`test_timezone_integration.py`)
which runs the full correction pipeline under 7 different system timezones.

## Test notes

### `test/shared.py` — test image helpers

- `decompress_sparse_image(gz_path, dest_path)` — decompresses a `.gz` sparse image
  to `dest_path` if not already present; no-op on subsequent calls.
- `prepare_sparse_image(gz_path, prefix='gopro_')` — decompresses to a cached
  location (reuses existing cache), then `cp --sparse=always` to a
  `tempfile.mkdtemp()` work dir. Returns `(work_dir, copy_path)` for safe
  isolated testing. Used by all tests that mount `sdcard.img`.
- `setup_loop_device(img_path)` / `teardown_loop_device(loop_dev, mount_point)`
  — loop device + mount lifecycle. Skips test on failure.
- `write_sparse(gz_path, img_path)` — low-level streaming decompressor.

### `test_debug_raw_btime.py` (debug tests)

- **test_01**: dd write/read at `100000 * 512` (51 MB). Uses `sync` only (no `drop_caches`).
- **test_05**: writes test pattern in `setUpClass` (before any FS modifications) to
  avoid kernel read-only remount. Uses 25 MB offset (`50000 * 512`).
- **test_06**: compares decoded mtime with `read_exfat_mtime_raw()` instead of
  `os.path.getmtime()` — bypasses the kernel exFAT UTC offset bug.
- **test_07**: batch `_fix_exfat_raw` on all 12 files with raw-block readback verification.

### CI workflows

Single `ci` workflow with a job matrix (`debug`, `unit`, `cluster`, `full`):

| Scope | What it runs | ~Duration |
|---|---|---|
| `debug` | `test_debug_raw_btime` (7 tests) | 30s |
| `unit` | `test.test_unit` (28 tests) | 5s |
| `cluster` | `test_cluster_coherence` | 45s |
| `full` | GUI correction + timezone integration | 3min |

## Mount strategy pattern

`src/strategies/mount.py` provides:

- `ImageMountStrategy` — creates loop device from `.img` file and mounts it
  (tries `udisksctl` first, falls back to `sudo losetup + mount`)
- `AlreadyMountedStrategy` — for paths already mounted (no-op)
- `detect_strategy(source)` — auto-selects strategy based on source type
- `REGISTRY` — dict for lookup by name

## Mtime strategy pattern

`src/strategies/mtime.py` provides:

- `OsUtimeMtimeStrategy` — uses `media.write_mtime()` (which calls `os.utime()`)
- `ExfatRawMtimeStrategy` — writes mtime via raw block, preserves existing btime
- `SkipMtimeStrategy` — no-op when exfat_raw btime already handles mtime
- The `Writer` selects strategy based on filesystem + btime method via
  `_resolve_mtime_strategy()`

## Capability probes

`src/probe.py` contains all probe functions, split from `env_check.py`:

| Probe | What it checks |
|---|---|
| `probe_stat_btime(path)` | `stat -c '%W'` birth time |
| `probe_statx_btime(path)` | `statx()` syscall STATX_BTIME |
| `probe_utime(path)` | `os.utime()` works on filesystem |
| `probe_btime(path)` | All of the above for a path |
| `probe_exfat_btime()` | Temp exFAT fs: stat/statx/raw/utime/dd/blockdev |
| `_probe_dd_nocache(device)` | `dd iflag=nocache` support |
| `_probe_blockdev_flush(device)` | `blockdev --flushbufs` support |

## Block device capability checks (env_check)

```
exFAT btime probe (temp filesystem):
  stat -c '%W'                 ✗   # kernel <6.12
  statx STATX_BTIME            ✗   # value is 0
  raw block readback           ✓   # always works (bypasses kernel)
  os.utime()                   ✗   # EPERM on exFAT
  dd iflag=nocache             ✓   # supported on coreutils 8.16+
  blockdev --flushbufs         ✓   # works on loop devices
  overall:                   ✓    # at least one method works
```
