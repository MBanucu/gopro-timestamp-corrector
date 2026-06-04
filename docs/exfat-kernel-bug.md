# exFAT Kernel Driver Cross-Mount Directory Entry Corruption

## Summary

Kernel **6.12.87** has a bug in the exFAT filesystem driver: under concurrent
write load across **two or more independent exFAT mounts**, dirty inodes from
one mount can be flushed to directory entries on **another mount**, corrupting
the corrected timestamps.

This was discovered and verified through **23 hypothesis tests** in
`test/hypothesis/`. The bug is **not fixable from userspace** — our code
works around it.

## Trigger

The bug is triggered by **ExifTool metadata writes** (`exiftool` binary writing
QuickTime metadata to video files). Plain `write()` syscalls (`open().write()`),
`touch` (`utimensat`), and our own raw-block writes (`fix_exfat_raw`) do
**not** trigger it.

| Operation | 2 mounts concurrent | Corruptions |
|---|---|---|
| ExifTool write (individual sessions) | Yes | **22/24** |
| ExifTool write (shared session, all files) | Yes | 0 |
| Plain `write()` through mount | Yes | 0 |
| `touch` (`utimensat`) | Yes | 0 |
| `fix_exfat_raw` (raw-block write) | Yes | 0 |
| `sync()` on mount B + ExifTool on mount A | Yes | **12/12** (H26) |

## Root Cause

ExifTool's internal `write()` pattern modifies file data through the VFS →
exFAT driver path. The driver:

1. Reads the directory entry via `sb_bread()` (loop device page cache)
2. Updates the in-memory inode with `mtime=now`
3. Marks the inode dirty

Under concurrent load across **multiple mounts**, the kernel exFAT driver's
`exfat_sync_fs()` writeback incorrectly flushes dirty inodes from one
superblock to directory entries on a different superblock. The driver does
not properly isolate inode writeback between independent mounts.

This is a **kernel bug** — `exfat_write_inode()` or the inode lookup
mechanism lacks superblock filtering.

## Why `sync()` Makes It Worse

The global `sync()` call (`subprocess.run(['sync'])`) triggers
`exfat_sync_fs()` on ALL mounted exFAT filesystems. This writeback:
- Reads the directory entry from the loop device's page cache
- Writes the in-memory mtime (=now from ExifTool) to the directory entry
- Does this on the WRONG mount due to the superblock isolation bug

Removing `sync()` from `fix_exfat_raw` eliminates this trigger. The
`os.fsync` on the backing file (in `ExfatRawIO.write()`) provides data
persistence without triggering the buggy driver writeback.

## Mitigations in Place

| Mitigation | File | What it does |
|---|---|---|
| `threading.Lock` | `exiftool_session.py` | Serializes ExifTool batch writes within one Python process |
| No `sync()` | `exfat_raw._ops` (external) | `os.fsync` on backing file is sufficient |
| No `os.utime()` | `exfat_raw._ops` (external) | Removed — driver reads stale DE cache on utime |
| Loop setup lock | `strategies/mount.py` | `fcntl.flock` on `/tmp/gopro_loop_setup.lock` prevents TOCTOU race |
| Backing-file I/O | `exfat_raw._strategies` (external) | `os.pread`/`os.pwrite` via backing file (loop device has separate page cache) |

## Production Safety

The production path (single mount, single pipeline via `Writer`) is **never**
affected by this bug because:

1. One pipeline = one ExifTool batch write + one set of `fix_exfat_raw` calls
2. No concurrent ExifTool writes across mounts
3. The `threading.Lock` serializes batch writes within the Writer

The bug only manifests when running **multiple independent correction
pipelines simultaneously on different mounts** — which only happens in the
parallel test suite (`run_parallel.py`/`nix run .#test`).

## How to Reproduce

Tests that demonstrated this bug have been removed. The mitigations remain in place (shared server serialization, no `sync()` after raw writes).

## Kernel Version

Confirmed on **Linux 6.12.87** (NixOS). May affect other 6.12.x versions.
The bug was not present or not reproducible on earlier kernels (6.6.x, 6.1.x).
