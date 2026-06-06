# AGENTS.md — gopro-timestamp-corrector

## Build & run

```sh
# All deps via Nix flakes; no system Python packages needed.
nix run .#gui          # launch GUI
nix run . -- --help    # CLI
# ExifTool TCP server is auto-spawned on demand
nix run .#test              # full test suite (parallel, coverage)
nix run .#test -- test_datepicker  # single module (omit test. prefix)
nix run .#test -- test_analysis test_gps  # multiple specific modules
nix develop            # dev shell with exiftool, e2fsprogs, etc.
```

- **Tests are NOT thread-safe**: always use `-j 1` when running against real
  (or loop) mounted filesystems to avoid cross-mount I/O races
  (see kernel exFAT bug below).  Higher `-j` values are safe for pure-unit
  tests that don't touch block devices.
- GUI tests run headlessly via Xvfb (no display required).
- Integration tests (`test_full_auto_integration.py`) need `sudo`/FUSE and are **not** run by `nix run .#test`. (exFAT raw-block tests moved to `exfat-raw` external package — see that repo.)
- `test/test_strategy.py` writes temp files, must be run from repo root.
- Large fixture: `test/sdcard.img.gz` (~14 MB, decompressed on first test run).
- Shortcut names for subdirectories: `nix run .#test -- hypothesis` runs
  all modules under `test/hypothesis/` (H9 and H11 only — other hypothesis
  tests moved to `exfat-raw` package).

## Architecture

| Layer | Directory | Entrypoint |
|---|---|---|
| CLI orchestrator | `src/` | `correct_timestamps.py` |
| ExifTool session | `src/` | `exiftool_session.py` — delegates to upstream pyexiftool TCP server |
| Plan / Planner | `src/` | `plan.py` — `Planner`, `CorrectionPlan`, `PlanBuilder`, `Instruction` |
| GUI app | `src/gui/` | `app.py` |
| GUI steps | `src/gui/steps/` | `directory.py`, `review.py`, `plan.py`, `run.py` |
| Mount strategies | `src/strategies/` | `mount.py` — `ImageMountStrategy`, `AlreadyMountedStrategy` |
| Mtime strategies | `src/strategies/` | `mtime.py` — `OsUtimeMtimeStrategy`, `ExfatRawMtimeStrategy`, `SkipMtimeStrategy` |
| Capability probes | `src/` | `probe.py` — `probe_stat_btime`, `probe_statx_btime`, `probe_exfat_btime`, etc. |
| Env check | `src/` | `env_check.py` — `check_env()`, `format_summary()`, CLI `--check` |
| Tests | `test/` | one file per area |

Key flow: `ExifToolSession()` (connects to shared server) → `analysis.analyze(session)` → `preview` calculator → `PlanBuilder.build()` (`Instruction` list) → `Writer(session=session)` I/O.

All internal times carry `tzinfo=timezone.utc`; display-layer DST via `zoneinfo`.

## Code conventions

- `pyproject.toml` pins `pyexiftool` (git dep, `MBanucu/pyexiftool@ef015c4`) and `exfat-raw` (PyPI).
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

## ExifTool server / client

Server/client support comes from the upstream `MBanucu/pyexiftool` fork at
`ef015c4` (v0.6.0, `feat/exiftool-server` branch).  `ExifToolSession()`
defaults to `connect='auto'` and delegates to `exiftool.client.ExifToolClient`,
which transparently connects to (or auto-spawns) a shared TCP server.

The project's own `src/exiftool_server.py`, `src/exiftool_client.py`,
`src/exiftool_protocol.py` and their tests were removed — all subsumed by
the upstream library.

Key file: `src/exiftool_session.py` — uses inheritance + `_executor` property
to unify `ExifToolHelper` (direct) and `ExifToolClient` (server) backends.

## exFAT raw block write & cache coherence

### `_fix_exfat_raw` (exfat-raw package, ``exfat_raw._ops``)

- Writes **both** creation time AND modification time in one raw-block access.
- **Does NOT call `sync()`** — the global ``sync()`` triggers the kernel exFAT driver's ``exfat_sync_fs()`` which incorrectly flushes dirty inodes from ALL mounts, causing cross-mount directory entry corruption on kernel 6.12.87. The ``os.fsync`` on the backing file (inside ``ExfatRawIO.write()``) is sufficient for data persistence.
- **Does NOT call `os.utime()`** — the kernel exFAT driver has its own
  in-memory directory-entry cache that is not invalidated by raw-block writes.
  Calling `os.utime()` after a raw-block write causes the driver to read the
  stale directory entry from its cache and overwrite the raw-block changes.
  The following mechanisms were tested empirically (12 runs × 2 workers each,
  24 worker attempts per strategy) and all **failed** to prevent the overwrite:
  ``fsync`` (file & dir), ``stat``, ``open``/``read``/``pread``, ``sleep``,
  ``sync``, ``subprocess touch -t``, double ``os.utime``, ``rewrite``.
  The only reliable option is to skip ``os.utime()`` entirely after a
  raw-block write.  The stale kernel cache is a cosmetic issue:
  ``stat`` shows the old mtime, but the raw block data is correct.
- **Does NOT call `drop_caches`** — on older kernels (<6.12), `drop_caches` with
  loop devices over sparse backing files causes `EIO` on subsequent reads.
  `sync` alone is sufficient for the raw block write to persist.

### exFAT driver cross-mount DE corruption (kernel 6.12.87)

The kernel exFAT driver has a bug where concurrent operations across
independent mounts can corrupt directory entries.  This was extensively
investigated via 23+ hypothesis tests (most now in the ``exfat-raw`` package):

| Trigger | Corruptions | Source |
|---|---|---|
| ExifTool writes on 2 mounts (individual sessions) | **22/24** | H19, H22, H23 |
| ExifTool writes on 2 mounts (shared session) | **0** | H20 |
| Plain ``write()`` through mount on 2 mounts | **0** | H20 |
| ``sync()`` on one mount + exiftool on another | **12/12** | H26 |
| ``fix_exfat_raw`` alone on 2 mounts | **0** | H19 |

**Root cause**: ExifTool's internal ``write()`` through the exFAT driver
dirties inodes with ``mtime=now``.  Under concurrent load across multiple
mounts, the driver's ``exfat_sync_fs()`` incorrectly flushes dirty inodes
from one mount to another mount's directory entries.

**Mitigations** (all in place):
1. ``ExifToolSession()`` defaults to ``connect='auto'`` — all production
   code routes through the shared server process, whose single-threaded
   accept loop serialises all exiftool operations.
2. ``sync()`` removed from ``fix_exfat_raw`` (the global sync was the
   primary trigger for cross-mount writeback).
3. ``os.fsync`` on the backing file (in ``ExfatRawIO.write()``) handles
   data persistence without triggering the buggy driver writeback.

### Server as primary path

Since `ExifToolSession()` now defaults to `connect='auto'` (server mode),
all production code (CLI, GUI) automatically routes exiftool operations
through the single server process.  The old cross-process `fcntl.flock`
lock has been removed as redundant — the server serialises all requests
in-process, eliminating write concurrency entirely across multiple CLI/GUI
invocations.  A separate client-side `fcntl.flock` (on ``{port_file}.client.lock``)
exists only to serialise concurrent callers during auto-spawn so that no
two callers ever try to start a server at the same time.
See `ExifTool server / client` above.

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

This is critical for timezone correctness.

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



### CI workflows

Single `ci` workflow with a 3-scope matrix:

| Scope | OS | What it runs | ~Duration |
|---|---|---|---|
| `unit` | ubuntu | 15 modules — all non-loop unit + GUI tests | 30s |
| `integration` | ubuntu | 4 loop-device modules (strategy, img, btime_gui_correction, full_auto_integration) | 2m |
| `macos` | macos | 8 non-GUI modules | 1m |

## Mount strategy pattern

`src/strategies/mount.py` provides:

- `ImageMountStrategy` — creates loop device from `.img` file and mounts it
  (tries `udisksctl` first, falls back to `sudo losetup + mount`)
  - Mount-point collision detection via `_existing_mount_points()`: records
    all mount paths occupied by loop devices **before** calling
    `loop-setup`.  After mount succeeds (via udisksctl or auto-mount), if
    the resulting path was already occupied, falls through to
    `_via_sudo_with()` which mounts to a unique tempdir.
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

## TODO

- **Thread safety**: Tests are not thread-safe against mounted filesystems.
  Make the test framework (loop device lifecycle, mount point collision detection,
  shared resource locking) and the production code (ExifTool session, raw block
  write serialisation) robust enough to tolerate parallel test execution,
  so that ``-j 1`` is no longer required.
