# Strategies for Setting Birth Time (btime) on exFAT Filesystems

> **Context**: GoPro cameras store videos on exFAT-formatted SD cards. When the camera battery dies, its clock resets to January 1, 2016 (the GoPro epoch). Tools like this project need to correct file timestamps, including the filesystem birth time (btime/creation time/crtime) — which Linux does not expose through standard syscalls like `utimensat`.

This document catalogs all known strategies for modifying btime on exFAT filesystems (both loopback images and physical SD cards) on Linux.

---

## Table of Contents

1. [FUSE + faketime (Current Approach)](#1-fuse--faketime-current-approach)
2. [Provenance Time — ptime syscall (Linux 6.17+)](#2-provenance-time--ptime-syscall-linux-617)
3. [Raw Block Device Manipulation](#3-raw-block-device-manipulation)
4. [System Clock Manipulation](#4-system-clock-manipulation)
5. [Custom FUSE Wrapper](#5-custom-fuse-wrapper)
6. [Kernel exFAT ioctl](#6-kernel-exfat-ioctl)
7. [Windows / macOS Interop](#7-windows--macos-interop)
8. [Comparison Matrix](#8-comparison-matrix)

---

## 1. FUSE + faketime (Current Approach)

### How it works

The standard kernel-mode exFAT driver (`exfat.ko`, since Linux 5.7) does not allow userspace to influence btime. This approach side-steps it entirely by using the FUSE-based exFAT driver instead:

1. **Unmount** the kernel-mode exFAT mount (`sudo umount /path`)
2. **Compute** a time offset from the correction delta (e.g., the GoPro clock was off by 2 days 3 hours → offset `-2d -3h`)
3. **Remount via FUSE** under `faketime`:
   ```
   faketime -f -{days}d -{hours}h -{minutes}m \
     mount.exfat-fuse <device> <path> \
     -o allow_other -o nonempty
   ```
4. **Write corrected timestamps** — when the tool uses `touch` or `utimensat` to set mtime, the FUSE daemon writes the file. Since the daemon runs under `faketime`, its time-of-day queries return the shifted time. The kernel records this shifted time as the file's btime.
5. **Teardown**: unmount the FUSE mount (`sudo umount`), kill the faketime process.

### Key detail

`faketime` works via `LD_PRELOAD`, intercepting libc time functions (`gettimeofday`, `clock_gettime`, `time`, etc.). It only affects userspace processes — the kernel's own `current_time()` is not intercepted. But because `mount.exfat-fuse` is a **FUSE daemon** (a userspace process that handles filesystem operations), all timestamps it writes pass through userspace time functions, making them interceptable.

### Requirements

| Item | Notes |
|------|-------|
| `libfaketime` | Provides `faketime` binary and `libfaketime.so` |
| `mount.exfat-fuse` | From `exfat` / `exfat-utils` / `exfat-fuse` package |
| `sudo` | For unmount/remount |
| Unmount of kernel exFAT | All open files must be closed |
| `allow_other` FUSE option | May need `/etc/fuse.conf` or user_allow_other |

### Pros

- Proven — implemented and working in this codebase
- No kernel changes needed
- Works on any Linux distribution

### Cons

- Requires unmount/remount — target cannot be in use
- `sudo` required for unmount
- FUSE may have performance overhead vs kernel driver
- `libfaketime` must be installed
- Fragile: if `faketime` crashes or the FUSE mount fails, the original mount must be restored
- On modern systems, the kernel exFAT driver may claim the device first; blacklisting the kernel module may be needed (`modprobe.blacklist=exfat`)

### References

- `src/btime.py` lines 113–173 in this repository
- [`mount.exfat-fuse` man page](https://man.archlinux.org/man/extra/exfat-utils/mount.exfat-fuse.8.en)
- [libfaketime upstream](https://github.com/wolfcw/libfaketime)

---

## 2. Provenance Time — ptime syscall (Linux 6.17+)

### How it works

The [provenance time (ptime) patch series](https://lwn.net/Articles/1066569/) by Sean Smith (submitted April 2026) adds a **new settable inode timestamp** to the Linux kernel. It resolves the long-standing impasse about whether btime should be mutable:

- **btime** remains immutable (forensic: "when was this inode born on this disk")
- **ptime** is settable and portable ("when was this content first created")

For **exFAT** (and FAT32, NTFS), ptime maps directly to the on-disk creation time field — no new on-disk structures are needed. This matches Windows/macOS behavior, where creation time is already settable via standard APIs.

### API

```
// Set ptime via utimensat with AT_UTIME_PTIME flag
// times[2] carries the desired provenance time
utimensat(AT_FDCWD, "/path/to/file", times, AT_UTIME_PTIME);

// Or lower-level: the file_setattr syscall (469, merged Linux 6.17)
// uses struct file_attr with VER1 extension containing ia_ptime
```

```
// Read ptime via statx with STATX_PTIME flag
struct statx buf;
statx(AT_FDCWD, "/path/to/file", 0, STATX_PTIME, &buf);
// buf.stx_ptime contains the provenance time
```

### Kernel patches (6 files)

| Patch | Scope |
|-------|-------|
| 1/6 | VFS ptime infrastructure: `ATTR_PTIME`, `STATX_PTIME`, `AT_UTIME_PTIME`, `struct iattr.ia_ptime` |
| 2/6 | btrfs: dedicated on-disk ptime field |
| 3/6 | ntfs3: mapped to NTFS Date Created |
| 4/6 | ext4: dedicated `i_ptime` field alongside immutable `i_crtime` |
| 5/6 | FAT32/vfat: mapped to creation time |
| 6/6 | exFAT: mapped to creation time |

For exFAT specifically (patch 6/6):

```c
// getattr: report creation time as ptime
if (request_mask & STATX_PTIME) {
    stat->result_mask |= STATX_PTIME;
    stat->ptime.tv_sec = ei->i_crtime.tv_sec;
    stat->ptime.tv_nsec = ei->i_crtime.tv_nsec;
}

// setattr: write ptime to i_crtime
if (attr->ia_valid & ATTR_PTIME) {
    exi->i_crtime = attr->ia_ptime;
}

// rename-over: preserve target creation time across atomic saves
// save target crtime, call __exfat_rename, restore
```

### Requirements

| Item | Notes |
|------|-------|
| Kernel ≥ 6.17 | `file_setattr` syscall (469) merged in 6.17 |
| ptime patches | The 6-patch series from DefendTheDisabled; tested on 6.19.11 |
| Userspace tool | A small C program or Python `ctypes` wrapper for `utimensat` / `statx` |

### Pros

- **Clean, standard kernel interface** — no LD_PRELOAD, no FUSE, no sudo
- Works with **both** kernel-mode exFAT **and** FUSE exFAT
- Rename-over preservation: atomic saves (`write-to-temp + rename`) preserve creation time
- Full permission model (owner or `CAP_FOWNER`, same as mtime)
- No filesystem unmount needed
- High precision: exFAT creation time has 10ms granularity (inherent to the format)

### Cons

- Requires kernel 6.17+ **plus** the ptime patches (not yet in mainline as of May 2026, though RFC v1 was submitted April 2026)
- No glibc wrapper yet — will need raw syscall (`syscall(SYS_utimensat, ...)`) until glibc adds `AT_UTIME_PTIME`
- Not available on older distributions

### Status

- `file_setattr` syscall 469 was merged in Linux 6.17 (by Andrey Albershteyn, Red Hat)
- RFC v1 ptime patch series posted April 5, 2026, tested on 6.19.11
- All 5 target filesystems (btrfs, ext4, ntfs3, FAT32, exFAT) passing runtime tests
- Discussion ongoing on linux-ext4 and linux-btrfs mailing lists

### References

- [LWN article on ptime](https://lwn.net/Articles/1066569/)
- [Patch series on lore.kernel.org](https://yhbt.net/lore/linux-ext4/20260405195007.1306-1-DefendTheDisabled@gmail.com/)
- [ptime GitHub project](https://github.com/DefendTheDisabled/linux-ptime)

---

## 3. Raw Block Device Manipulation

### How it works

exFAT has a well-documented on-disk structure. Each file has a **file directory entry** (a 32-byte structure in the directory cluster chain) that contains four creation time fields:

| Field | Size | Description |
|-------|------|-------------|
| `CreateTz` | 1 byte | Timezone offset from UTC (0=UTC, 0x80=local) |
| `CreateTimeMs` | 1 byte | Millisecond value (0–199, in 10ms increments) |
| `CreateTime` | 2 bytes | Time of day: `hour<<11 | minute<<5 | second/2` (2-second granularity) |
| `CreateDate` | 2 bytes | Date: `(year-1980)<<9 | month<<5 | day` |

A tool can:
1. Open the block device directly (`/dev/sdX1` or a loopback image)
2. Parse the exFAT Boot Sector, FAT Allocation Table, and cluster chain
3. Navigate directory hierarchies to find the target file's directory entry
4. Read the current creation time fields, then overwrite them with the desired timestamp
5. Update the directory entry checksum if required
6. Flush writes

### Example: locating a file entry

```
Boot Sector → FAT (cluster chain) → Root Directory cluster
  → File Entry Set:
    [0] File Directory Entry   (creation time, attributes, etc.)
    [1] Stream Extension Entry (file size, name hash)
    [2+] Name Hash Entries     (up to 15 entries for long names)
```

The creation time fields are at offsets 0x0A–0x0F in the File Directory Entry.

### Requirements

| Item | Notes |
|------|-------|
| Root or `CAP_SYS_RAWIO` | For block device access |
| exFAT format knowledge | Or use a library like `libexfat` |
| Filesystem unmounted | Safer, but technically possible while mounted (with risks) |
| Checksum recalculation | The directory entry set has a checksum that must be updated |

### Pros

- Works on any exFAT volume regardless of kernel version
- Can batch-process many files efficiently
- No external dependencies beyond a tool

### Cons

- **High risk** — a wrong write can corrupt the filesystem
- Complex: must implement exFAT directory traversal (handling fragmented cluster chains, long file names, etc.)
- Requires block device access (root)
- Safer to do unmounted, which means it can't be done while files are in use
- No existing mature open-source tool for this specific purpose
- Must handle endianness correctly (exFAT is little-endian)

### Existing tools

No dedicated tool exists yet for modifying exFAT creation time via raw blocks. Related tools:

- **`debugfs`** (e2fsprogs) — for ext2/3/4 only, no exFAT equivalent
- **`exfatprogs`** — includes `dump.exfat`, `tune.exfat`, `fsck.exfat`, `mkfs.exfat`; none currently support setting file creation time
- **`fatsort`** — sorts FAT files by changing inode order, not timestamps

### Implementation sketch (Python)

```python
import struct

def set_btime_exfat(device, file_path, target_ts):
    """
    Set creation time of file_path on exFAT volume at device.

    Steps:
    1. Read boot sector to get volume parameters
    2. Navigate directory cluster chain to find file entry
    3. Encode target_ts as exFAT time/date fields
    4. Write fields and update checksum
    """
    # ... complex implementation ...
    # exFAT timestamp encoding:
    tm = gmtime(target_ts)
    time_2s = (tm.tm_hour << 11) | (tm.tm_min << 5) | (tm.tm_sec // 2)
    date = ((tm.tm_year - 1980) << 9) | (tm.tm_mon << 5) | tm.tm_mday
    time_ms = (tm.tm_sec % 2) * 100 + (tm.tm_usec // 10000)  # 10ms increments
```

---

## 4. System Clock Manipulation

### How it works

The most brute-force approach: temporarily rewind the system clock, create/modify files, then restore the clock.

1. Stop NTP: `sudo timedatectl set-ntp false`
2. Set system time to the desired btime value: `sudo date -s @<unix_timestamp>`
3. Touch files with `touch -m` (or copy/create files) — btime will be set to the fake clock
4. Restore clock: `sudo timedatectl set-ntp true`
5. Sync with NTP pool

### Requirements

| Item | Notes |
|------|-------|
| Root | For `date -s` and `timedatectl` |
| NTP restart | May take seconds to minutes to re-sync |

### Pros

- Works on **any** filesystem (ext4, exFAT, NTFS, btrfs, etc.)
- No special tools beyond standard Unix utilities
- No kernel or filesystem driver hacks

### Cons

- **Extremely disruptive** — affects all running processes, systemd timers, cron jobs, logging timestamps, database transactions, etc.
- Time-sensitive operations (certificate validation, OAuth tokens, etc.) may fail
- NTP re-sync may take time and may step the clock, causing further disruption
- If the system has dependent services (databases, web servers), they may behave unpredictably
- Not suitable for production systems

### References

- `src/btime.py` lines 176–223 in this repository (clock setup/teardown)
- `src/btime.py` lines 210–223 (`_fix_clock_set_time`)

---

## 5. Custom FUSE Wrapper

### How it works

Instead of using `faketime` to globally fake the clock for `mount.exfat-fuse`, write a **dedicated FUSE filesystem** that wraps the underlying exFAT mount and intercepts timestamp-related operations.

The wrapper FUSE filesystem would:
1. Mount the real exFAT filesystem at a hidden path
2. Expose a FUSE mount at the target path
3. In its `setattr` / `write` / `create` handlers, forward operations to the real filesystem but override the creation time to the desired value

Since FUSE `setattr` handlers receive the full `struct stat` (including `st_birthtime` on platforms that support it), a wrapper could call `utimensat` on the underlying file with the desired timestamps.

A lighter variant: use **bindfs** with a custom timestamp map, though bindfs does not support btime manipulation.

### Requirements

| Item | Notes |
|------|-------|
| FUSE development | Python (`fusepy`), C, or Go |
| Kernel exFAT or FUSE exFAT | The underlying filesystem provider |
| Development effort | Significant |

### Pros

- No system-wide clock disruption
- No `sudo` for time manipulation (FUSE mount itself may need `allow_other`)
- Can selectively override timestamps per-file or per-operation
- Could be combined with ptime for a clean solution

### Cons

- Significant development effort
- FUSE overhead on all I/O operations
- Handling all FUSE operations correctly (readdir, create, write, truncate, rename, etc.) is complex
- Must handle edge cases: concurrent access, permissions, symlinks (rare on exFAT), etc.

---

## 6. Kernel exFAT ioctl

### How it works

The kernel-mode exFAT driver (since Linux 5.7, contributed by Samsung) already has `EXFAT_IOC_GET_ATTR` and `EXFAT_IOC_SET_ATTR` ioctls (merged in Linux 6.7) for reading/writing file attributes. However, these do **not** currently expose creation time.

A kernel patch could add a new ioctl (e.g., `EXFAT_IOC_SET_CRTIME`) that directly modifies `EXFAT_I(inode)->i_crtime` and marks the inode dirty so it gets written to disk:

```c
case EXFAT_IOC_SET_CRTIME: {
    struct timespec64 ts;
    if (copy_from_user(&ts, argp, sizeof(ts)))
        return -EFAULT;
    EXFAT_I(inode)->i_crtime = ts;
    mark_inode_dirty(inode);
    return 0;
}
```

This would then be callable from userspace:

```c
ioctl(fd, EXFAT_IOC_SET_CRTIME, &desired_timespec);
```

### Requirements

| Item | Notes |
|------|-------|
| Kernel development | Custom module or patched kernel |
| Userspace tool | Small C program to issue the ioctl |
| Patched kernel on target machines | Requires deployment |

### Pros

- Very clean API from userspace
- Works on live, mounted filesystems
- No unmount, no FUSE, no faketime
- Minimal kernel code change

### Cons

- Requires maintaining a kernel patch
- Not upstream (would need to go through linux-fsdevel / LKML)
- Not available on stock kernels
- If the filesystem is also mounted elsewhere (e.g., via USB on another OS), the in-memory change won't be reflected until flushed

---

## 7. Windows / macOS Interop

For completeness: the simplest practical strategy is often to use an OS that natively supports setting creation time.

### Windows

```powershell
# PowerShell
(Get-Item "file.mp4").CreationTime = Get-Date "2016-01-01 00:00:00"
```

### macOS

```bash
# Set creation time (BSD variant of touch supports -B for birth time)
touch -B "201601010000" file.mp4

# Or via SetFile (Xcode command line tools)
SetFile -d "01/01/2016 00:00:00" file.mp4
```

### Linux via Wine

`wine` with native Windows API calls can potentially set exFAT creation time through the Windows filesystem API mapping.

### Pros

- Works perfectly on the target platform
- No low-level hacks

### Cons

- Requires Windows or macOS
- Not scriptable cross-platform without virtualization
- Doesn't help when the correction must be done on Linux

---

## 8. Comparison Matrix

| Strategy | Complexity | Risk | Requires Root | Works Mounted | Kernel Deps | External Tools | Ready |
|----------|-----------|------|-------------|--------------|------------|---------------|-------|
| **FUSE + faketime** | Medium | Medium | Yes (unmount) | No | No | libfaketime, mount.exfat-fuse | ✅ |
| **ptime syscall** | Low | Low | No | Yes | ≥ 6.17 + patches | Custom tool (`utimensat`+AT_UTIME_PTIME) | 🚧 Patches submitted |
| **Raw block** | High | High | Yes | Not recommended | No | Custom tool | ❌ |
| **Clock manipulation** | Low | Very High | Yes | Yes | No | date, timedatectl | ✅ |
| **Custom FUSE** | High | Low | Maybe (mount) | Yes | No | FUSE library | ❌ |
| **Kernel ioctl** | Medium | Low | No | Yes | Custom patch | Custom tool | ❌ |
| **Windows/macOS** | N/A* | None | N/A* | Yes | N/A* | OS built-in | ✅ |

\* N/A = Not on Linux; requires alternate OS.

---

## Recommendation

For the **GoPro timestamp corrector** use case:

1. **Continue using FUSE + faketime** as the primary strategy for exFAT. It works reliably, is already implemented, and has no kernel version dependency.

2. **Add ptime support as a future backend** once the ptime patches land in a stable kernel that NixOS and major distros ship. The API is cleaner and requires no unmount. When available, it should become the default for kernel-mode exFAT mounts, with FUSE+faketime as fallback.

3. **Raw block manipulation** and **kernel ioctl** are overkill for this use case. They may be justified for a dedicated mass-correction tool but not for a general-purpose consumer utility.

4. **Clock manipulation** should remain as the last-resort fallback for filesystems that support neither FUSE nor ptime.
