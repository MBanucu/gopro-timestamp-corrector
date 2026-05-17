# Raw exFAT Block Manipulation — Implementation Report

> **Date**: May 2026
> **Project**: GoPro Timestamp Corrector
> **Strategy**: Direct exFAT block device manipulation (`exfat_raw`)
> **Status**: Implemented, tested, and committed

---

## Table of Contents

1. [exFAT Filesystem Internals](#1-exfat-filesystem-internals)
   - [1.1 Boot Sector](#11-boot-sector)
   - [1.2 File Allocation Table (FAT)](#12-file-allocation-table-fat)
   - [1.3 Cluster Heap](#13-cluster-heap)
   - [1.4 Directory Entry Sets](#14-directory-entry-sets)
   - [1.5 Creation Time Encoding](#15-creation-time-encoding)
   - [1.6 Set Checksum (CRC-16/CCITT)](#16-set-checksum-crc-16ccitt)
2. [Implementation Deep Dive](#2-implementation-deep-dive)
   - [2.1 Architecture Overview](#21-architecture-overview)
   - [2.2 Boot Sector Parser (`_exfat_parse_boot`)](#22-boot-sector-parser-_exfat_parse_boot)
   - [2.3 Device I/O Layer (`_exfat_read/write_device`)](#23-device-io-layer-_exfat_readwrite_device)
   - [2.4 FAT Traversal (`_exfat_read_fat`, `_exfat_cluster_chain`)](#24-fat-traversal-_exfat_read_fat-_exfat_cluster_chain)
   - [2.5 Directory Scanner (`_exfat_find_in_dir`)](#25-directory-scanner-_exfat_find_in_dir)
   - [2.6 Filename Reconstruction (`_exfat_entry_name`)](#26-filename-reconstruction-_exfat_entry_name)
   - [2.7 Time Encoding (`_exfat_encode_time`)](#27-time-encoding-_exfat_encode_time)
   - [2.8 Entry Modification and CRC Update](#28-entry-modification-and-crc-update)
   - [2.9 Cluster Write and Cache Flush](#29-cluster-write-and-cache-flush)
   - [2.10 Main Orchestrator (`_fix_exfat_raw`)](#210-main-orchestrator-_fix_exfat_raw)
   - [2.11 Method Registration](#211-method-registration)
3. [The Field Offset Bug](#3-the-field-offset-bug)
   - [3.1 Symptoms](#31-symptoms)
   - [3.2 Root Cause](#32-root-cause)
   - [3.3 Debugging Process](#33-debugging-process)
   - [3.4 Fix](#34-fix)
4. [Test Coverage](#4-test-coverage)
   - [4.1 Unit Tests](#41-unit-tests)
   - [4.2 Integration Tests](#42-integration-tests)
   - [4.3 Debug Helper](#43-debug-helper)
   - [4.4 Test Infrastructure](#44-test-infrastructure)
5. [References](#5-references)

---

## 1. exFAT Filesystem Internals

This section documents the exFAT on-disk structures relevant to modifying file creation time. The exFAT filesystem (Extended File Allocation Table, introduced by Microsoft in 2006) is a modern successor to FAT32 designed for flash storage.

### 1.1 Boot Sector

The boot sector occupies the first 512 bytes of an exFAT volume. Its structure diverges significantly from legacy FAT boot sectors. Key fields for traversal:

| Offset | Size | Field | Description |
|--------|------|-------|-------------|
| 0x00 | 3 | JumpBoot | Jump instruction (`EB 76 90`) |
| 0x03 | 8 | OEMName | `"EXFAT   "` |
| 0x50 | 4 | FatOffset | Sector offset of first FAT (multiply by BytesPerSector) |
| 0x54 | 4 | FatLength | Length of each FAT in sectors |
| 0x58 | 4 | ClusterHeapOffset | Sector offset of cluster heap |
| 0x5C | 4 | ClusterCount | Total number of clusters |
| 0x60 | 4 | RootDirCluster | First cluster of root directory |
| 0x6C | 1 | BytesPerSectorShift | Log2 of bytes per sector (e.g. 9 = 512) |
| 0x6D | 1 | SectorsPerClusterShift | Log2 of sectors per cluster (e.g. 0 = 1, 9 = 512) |
| 0x6E | 2 | NumberOfFats | Typically 1 |
| 0x70 | 4 | VolumeSerialNumber | Unique volume identifier |
| 0x1FE | 2 | BootSignature | `0xAA55` |

The implementation reads these fields in `_exfat_parse_boot()` at `src/btime.py:282`. All multi-byte fields are little-endian.

From these raw fields we compute:

```
bytes_per_sector = 1 << BytesPerSectorShift
sectors_per_cluster = 1 << SectorsPerClusterShift
cluster_size = bytes_per_sector * sectors_per_cluster
fat_offset = FatOffset * bytes_per_sector        (byte offset)
cluster_heap_offset = ClusterHeapOffset * bytes_per_sector  (byte offset)
```

### 1.2 File Allocation Table (FAT)

The exFAT FAT is a contiguous array of 32-bit entries, one per cluster. Each entry stores either:

- `0x00000000` – Free cluster
- `0x00000001` – Reserved (not used)
- `0x00000002` to `0x0FFFFFF7` – Next cluster in the chain (points to the following cluster)
- `0x0FFFFFF8` to `0x0FFFFFFF` – End-of-chain marker
- `0x0FFFFFF7` – Bad cluster

Cluster 0 and 1 are reserved. Usable data clusters start at index 2.

The FAT entry at index `N` is located at byte offset:

```
fat_offset + N * 4
```

Only the low 28 bits of each entry are used; the high 4 bits are reserved.

Implementation in `_exfat_read_fat()` (`src/btime.py:301`):

```python
off = boot['fat_offset'] + cluster * 4
data = _exfat_read_device(device, off, 4)
return struct.unpack_from('<I', data, 0)[0] & 0x0FFFFFFF
```

### 1.3 Cluster Heap

The cluster heap starts at byte offset `ClusterHeapOffset * BytesPerSector`. Cluster `N` (where N ≥ 2) is located at:

```
cluster_heap_offset + (N - 2) * cluster_size
```

Reading and writing clusters uses this formula directly in `_exfat_read_clusters()` and `_exfat_write_clusters()` (`src/btime.py:327-335`).

### 1.4 Directory Entry Sets

exFAT directories are stored as linked lists of clusters (via the FAT), where each cluster contains a linear array of 32-byte entries. Directory entries are grouped into **entry sets** — a file or subdirectory is represented by a contiguous group of entries.

The three entry types relevant to file traversal:

#### File Directory Entry (Type `0x85`)

| Offset | Size | Field | Description |
|--------|------|-------|-------------|
| 0x00 | 1 | EntryType | `0x85` = File directory entry |
| 0x01 | 1 | SecondaryCount | Number of secondary entries following this one |
| 0x02 | 2 | SetChecksum | CRC-16/CCITT over all entries in the set |
| 0x04 | 2 | FileAttributes | Standard DOS attributes |
| 0x06 | 2 | Reserved1 | Must be zero |
| **0x08** | **2** | **CreateTime** | **Time of day: `hour<<11 | minute<<5 | sec//2`** |
| **0x0A** | **2** | **CreateDate** | **Date: `(year-1980)<<9 | month<<5 | day`** |
| 0x0C | 2 | ModifyTime | Same encoding as CreateTime |
| 0x0E | 2 | ModifyDate | Same encoding as CreateDate |
| 0x10 | 2 | AccessTime | Same encoding (may be zero) |
| 0x12 | 2 | AccessDate | Same encoding (may be zero) |
| **0x14** | **1** | **CreateTimeMs** | **Milliseconds: `(sec%2)*100 + ms//10` (0–199)** |
| **0x15** | **1** | **CreateTimezone** | **0 = UTC, 0x80 = local, else offset from UTC** |
| 0x16 | 1 | ModifyTimeMs | Same encoding as CreateTimeMs |
| 0x17 | 1 | ModifyTimezone | Same as CreateTimezone |
| 0x18 | 2 | AccessTimeMs | Same encoding |
| 0x1A | 1 | AccessTimezone | Same as CreateTimezone |
| 0x1B–0x1F | 5 | Reserved2 | Must be zero |

**Critical detail about field layout**: The kernel struct `struct exfat_dentry` in `exfat.h` places `create_time` at offset 0x08 and `create_date` at offset 0x0A, with `reserved1` at 0x06. This is the **same layout** as the on-disk structure. An early implementation bug (see §3) used offset 0x06 for CreateDate, which overwrote the Reserved1 field instead.

#### Stream Extension Entry (Type `0xC0`)

| Offset | Size | Field |
|--------|------|-------|
| 0x00 | 1 | EntryType (`0xC0`) |
| 0x01 | 1 | GeneralSecondaryFlags |
| 0x02 | 1 | NameLength (character count of filename) |
| 0x03 | 1 | NameHash |
| 0x04 | 2 | Reserved |
| 0x06 | 8 | ValidDataLength |
| 0x0E | 4 | Reserved |
| **0x14** | **4** | **FirstCluster** (of the file's data or subdirectory) |
| 0x18 | 8 | DataLength |

For subdirectories, the `FirstCluster` field (offset 0x14) points to the first cluster of the child directory, which is used for recursive tree traversal.

#### File Name Entry (Type `0xC1`)

| Offset | Size | Field |
|--------|------|-------|
| 0x00 | 1 | EntryType (`0xC1`) |
| 0x01 | 1 | GeneralSecondaryFlags |
| **0x02** | **30** | **NameChars** (15 UTF-16LE code units) |

Filenames longer than 15 characters use multiple name entries consecutively. The name is null-terminated within the character array.

#### End-of-Directory Marker (Type `0x00`)

When the scanner encounters an entry type of `0x00`, it has reached the end of valid entries in the current directory — no more entries follow.

### 1.5 Creation Time Encoding

exFAT encodes creation time in three fields with 10ms granularity (not nanosecond). This is a legacy of the FAT filesystem lineage where 2-second granularity was the norm, and the millisecond field adds finer resolution.

**Date encoding** (16 bits):

```
bits 15–9:  year − 1980  (0–127, supports 1980–2107)
bits 8–5:   month        (1–12)
bits 4–0:   day           (1–31)
```

**Time encoding** (16 bits):

```
bits 15–11: hour   (0–23)
bits 10–5:  minute (0–59)
bits 4–0:   sec/2  (0–29)
```

Note that seconds have 2-second granularity in the time word. The odd/even second is encoded in the millisecond field.

**Millisecond encoding** (8 bits, 0–199):

```
(sec % 2) * 100 + ms // 10
```

This gives 10ms increments. Values 0–199 inclusive. The `sec % 2` term encodes whether the truncated second was odd (100) or even (0). The `ms // 10` term carries the millisecond remainder in 10ms steps.

**Implementation** (`_exfat_encode_time` at `src/btime.py:249`):

```python
date_word = ((year - 1980) << 9) | (month << 5) | day
time_word = (hour << 11) | (minute << 5) | (sec // 2)
time_ms = (sec % 2) * 100 + (ms // 10)
```

### 1.6 Set Checksum (CRC-16/CCITT)

The exFAT directory entry set uses CRC-16/CCITT with the following parameters:

- **Polynomial**: `0x1021` (standard CRC-16/CCITT: `x^16 + x^12 + x^5 + 1`)
- **Initial value**: `0x0000`
- **Reflect input**: No
- **Reflect output**: No
- **XOR output**: `0x0000`

The checksum covers all entries in the set sequentially. For each 32-byte entry, the two bytes at offsets 2–3 (the SetChecksum field itself) are **zeroed** during the calculation, then the result replaces those bytes.

Implementation (`_exfat_entry_set_crc` at `src/btime.py:239`):

```python
crc = 0
for entry in entries:
    crc = _exfat_crc16(entry[:2], crc)    # bytes 0-1
    crc = _exfat_crc16(b'\x00\x00', crc)  # bytes 2-3 (zeroed)
    crc = _exfat_crc16(entry[4:], crc)    # bytes 4-31
```

The bit-level CRC implementation (`_exfat_crc16` at `src/btime.py:229`) processes each byte top-bit-first (no reflection), using the standard polynomial:

```python
for byte in data:
    crc ^= byte << 8
    for _ in range(8):
        crc = (crc << 1) ^ 0x1021 if crc & 0x8000 else crc << 1
    crc &= 0xFFFF
```

Verified against the standard CRC-16/CCITT check value: `_exfat_crc16(b'123456789')` = `0x31C3`.

---

## 2. Implementation Deep Dive

### 2.1 Architecture Overview

The implementation follows a layered design in `src/btime.py`:

```
_fix_exfat_raw()              ← Main entry point: resolves paths, orchestrates
    ├─ _resolve_device()      ← Maps file path → block device (/dev/loopN, /dev/sda1)
    ├─ _resolve_mount_point() ← Finds mount point via findmnt
    ├─ _exfat_parse_boot()    ← Reads boot sector, extracts volume geometry
    ├─ _exfat_find_in_dir()   ← Scans directory for named entry (recursive)
    │   ├─ _exfat_collect_dir() → Reads full directory cluster chain into buffer
    │   │   ├─ _exfat_cluster_chain() → Walks FAT to get ordered cluster list
    │   │   └─ _exfat_read_clusters() → Reads each cluster's raw bytes
    │   └─ _exfat_entry_name() → Reconstructs UTF-16LE filename from entry set
    ├─ _exfat_encode_time()   ← Encodes datetime → date_word/time_word/time_ms
    ├─ _exfat_entry_set_crc() ← Recalculates CRC-16/CCITT for modified entries
    └─ _exfat_write_clusters()→ Writes modified cluster back + cache flush
```

### 2.2 Boot Sector Parser (`_exfat_parse_boot`)

**File**: `src/btime.py:282`

Reads 512 bytes from offset 0 of the block device. Validates the boot signature at offset 510 (`0xAA55`). Extracts:

- `BytesPerSectorShift` at offset `0x6C` → compute `bytes_per_sector = 1 << shift`
- `SectorsPerClusterShift` at offset `0x6D` → compute `sectors_per_cluster = 1 << shift`
- `FatOffset` (uint32 LE) at offset `0x50` → multiply by `bytes_per_sector` for byte offset
- `ClusterHeapOffset` (uint32 LE) at offset `0x58` → multiply by `bytes_per_sector` for byte offset
- `RootDirCluster` (uint32 LE) at offset `0x60` → first cluster of root directory

Returns a dict with:
```python
{
    'bytes_per_sector':  512,
    'sec_per_cluster':   1,       # or more
    'cluster_size':      512,     # bytes_per_sector * sec_per_cluster
    'fat_offset':        0x20000, # byte offset in device
    'cluster_heap_offset': 0x22000,
    'root_cluster':      4,       # cluster index (≥ 2)
}
```

### 2.3 Device I/O Layer (`_exfat_read/write_device`)

**File**: `src/btime.py:263-279`

These are thin wrappers around `sudo dd` for reading and writing arbitrary byte ranges on the block device. Using `dd` with `bs=1` allows exact byte-level addressing.

**Read**: `sudo dd if=<device> bs=1 skip=<offset> count=<size> status=none`
**Write**: Write data to a temp file, then `sudo dd if=<temp> of=<device> bs=1 seek=<offset> count=<len> status=none`

Notes:
- Requires `sudo` for block device access
- `bs=1` is slow for large reads but adequate for metadata-sized operations (typically 512 bytes to 4 KB per cluster)
- The write helper uses a `NamedTemporaryFile` to pass data to `dd` via stdin (indirection needed because `dd` operates on files, not pipes)

### 2.4 FAT Traversal (`_exfat_read_fat`, `_exfat_cluster_chain`)

**File**: `src/btime.py:301-324`

`_exfat_read_fat(boot, device, cluster)`: Reads the 4-byte FAT entry for a given cluster index and returns its value masked to 28 bits.

`_exfat_cluster_chain(boot, device, start_cluster)`: Walks the FAT from a starting cluster, following next-cluster pointers until an end-of-chain marker (`≥ 0x0FFFFFF8`) is reached. Returns the ordered list of cluster indices. Includes cycle detection (via a `seen` set) to handle corrupted filesystems gracefully.

### 2.5 Directory Scanner (`_exfat_find_in_dir`)

**File**: `src/btime.py:362-388`

This is the core search function. Given a directory's starting cluster, it:

1. **Collects** the directory's full content by reading all clusters in the chain into a contiguous bytearray (`_exfat_collect_dir`)
2. **Iterates** over 32-byte entries within the buffer
3. For each entry with type `0x85` (File Directory Entry):
   - Reads `SecondaryCount` from byte 1
   - Collects the full entry set (1 + SecondaryCount entries)
   - Extracts filename from secondary name entries (type `0xC1`)
   - If the filename matches the target, returns the location and entry set

**Return value**:
```python
(
    chain,           # list[int] — cluster indices in the directory's chain
    cluster_idx,     # int — which cluster in the chain contains the entry
    offset_in_cluster,  # int — byte offset within that cluster
    secondary_count, # int — number of secondary entries
    entry_bytes      # list[bytes] — raw 32-byte entries of the set
)
```

Returns `None` if the entry is not found or an end-of-directory marker (`0x00`) is hit.

### 2.6 Filename Reconstruction (`_exfat_entry_name`)

**File**: `src/btime.py:348-359`

Iterates through name entries (type `0xC1`) in the entry set's secondary entries. Each entry carries 30 bytes = 15 UTF-16LE code units. Characters are decoded via `struct.unpack('<H')` until a null terminator is found.

GoPro filenames are ASCII (`GL010063.LRV`, `GX010064.MP4`, etc.), but the implementation correctly handles full UTF-16LE.

### 2.7 Time Encoding (`_exfat_encode_time`)

**File**: `src/btime.py:249-260`

Takes a Python `datetime` (timezone-aware) and returns three packed values:

| Return value | Type | Range | Encoding |
|-------------|------|-------|----------|
| `date_word` | int (16-bit) | 0–65535 | `(year-1980)<<9 | month<<5 | day` |
| `time_word` | int (16-bit) | 0–65535 | `hour<<11 | minute<<5 | sec//2` |
| `time_ms` | int (8-bit) | 0–199 | `(sec%2)*100 + ms//10` |

The encoding discards microsecond precision beyond 10ms (exFAT's maximum resolution).

### 2.8 Entry Modification and CRC Update

**File**: `src/btime.py:450-461`

Once the file's entry set is located, the first entry (File Directory Entry, type `0x85`) is modified:

```python
entry = bytearray(fentries[0])  # mutable copy
date_word, time_word, time_ms_val = _exfat_encode_time(utc)
struct.pack_into('<H', entry, 0x08, time_word)   # CreateTime
struct.pack_into('<H', entry, 0x0A, date_word)   # CreateDate
entry[0x14] = time_ms_val                         # CreateTimeMs
entry[0x15] = 0                                   # CreateTimezone = UTC
```

Then the set checksum is recalculated across all entries:

```python
modified_entries = [bytes(entry)] + list(fentries[1:])
crc = _exfat_entry_set_crc(modified_entries)
struct.pack_into('<H', entry, 2, crc)  # Write CRC into SetChecksum field
modified_entries[0] = bytes(entry)
```

### 2.9 Cluster Write and Cache Flush

**File**: `src/btime.py:463-478`

The modified entries are spliced into the original cluster buffer:

```python
cluster_data = _exfat_read_clusters(boot, device, [fchain[fci]])[0]
cluster_buf = bytearray(cluster_data)
off = foff
for e in modified_entries:
    cluster_buf[off:off + 32] = e
    off += 32
_exfat_write_clusters(boot, device, [fchain[fci]], [bytes(cluster_buf)])
```

Only the single cluster containing the file's directory entry is written — not the entire directory. This minimizes the write window and reduces risk.

After writing, caches are flushed to force the kernel to re-read the modified blocks:

```python
subprocess.run(['sync'])
subprocess.run(['sudo', 'sh', '-c', 'echo 3 > /proc/sys/vm/drop_caches'],
               capture_output=True)
```

`drop_caches` with value 3 clears page cache, dentries, and inodes. This ensures the next `statx()` call reads from disk rather than the stale cached copy.

### 2.10 Main Orchestrator (`_fix_exfat_raw`)

**File**: `src/btime.py:391-478`

The orchestrator ties everything together:

1. **Resolve block device** → `_resolve_device()` maps the file to e.g. `/dev/loop0`
2. **Parse boot sector** → `_exfat_parse_boot()` gets volume geometry
3. **Find mount point** → `_resolve_mount_point()` via `findmnt`
4. **Compute relative path** → e.g. `DCIM/100GOPRO/GL010063.LRV`
5. **Traverse directory tree** → for each path component except the filename:
   - Call `_exfat_find_in_dir()` with the current directory cluster
   - Extract the subdirectory's first cluster from the Stream Extension Entry
6. **Find file entry** → `_exfat_find_in_dir()` in the leaf directory
7. **Modify creation time** → update fields and recalculate CRC
8. **Write cluster** → single-cluster write + cache flush

### 2.11 Method Registration

**File**: `src/btime.py:40-84`

The `exfat_raw` method is registered in four places:

| Function | Registration |
|----------|-------------|
| `resolve_method('exfat_raw', _)` | Returns `'exfat_raw'` |
| `needs_processing_after(x)` | Returns `True` for `'exfat_raw'` (like `'debugfs'`) |
| `fix_file('exfat_raw', ...)` | Routes to `_fix_exfat_raw()` |
| `setup/teardown` | No-op for `exfat_raw` (no setup needed) |

Note: `resolve_method('auto', 'exfat')` returns `'exfat_raw'` since May 2026, making it the default for exFAT. Explicitly requesting `'fuse'` or `'clock'` still works for manual override.

---

## 3. The Field Offset Bug

### 3.1 Symptoms

The initial implementation of `_fix_exfat_raw` appeared to work (no errors), but the btime after correction decoded to approximately **1979-12-30** regardless of the target datetime. The exFAT date range starts at 1980, so a date around 1979 suggests the `year-1980` calculation underflowed, meaning the `date_word` field was effectively zero (year = 1980+0 = 1980, but with the wrong month/day bits producing an invalid date).

### 3.2 Root Cause

The kernel's `struct exfat_dentry` has `reserved1` (2 bytes) at offset 0x06, immediately after `file_attributes` (2 bytes at offset 0x04). The original code placed `create_date` at offset 0x06, which wrote into the reserved field instead of `create_date` at 0x0A.

**Incorrect layout (first implementation):**

| Offset | Field | Value written | Kernel expects |
|--------|-------|---------------|----------------|
| 0x06 | Write: `date_word` | `0x5CAE` | **Reserved1** (ignored) |
| 0x08 | Write: `time_word` | ... | **CreateTime** ✓ |
| 0x0A | Write: `time_ms_val` | `0x00` | **CreateDate** (set to 0 → year 1980) |
| 0x0C | (left alone) | ... | ModifyTime |
| 0x14 | (left alone) | ... | **CreateTimeMs** |

The `read_btime()` function reads btime via `statx()`. Since `CreateDate` at offset 0x0A was overwritten with `time_ms_val` (a 1-byte value in a 2-byte field), and `CreateTimeMs` at 0x14 was left untouched, the decoded date was garbage, usually reading as year 1979.

**Root cause**: I assumed the creation time fields were contiguous at offsets 0x06–0x0D based on a simplified online description of the exFAT file entry. I did not consult the actual kernel source (`exfat.h`) or the exFAT specification until after the debug session.

### 3.3 Debugging Process

The bug was discovered using `test/debug_exfat.py`, which:

1. Sets up a loop device with the test SD card image
2. Reads the raw directory cluster bytes before any modification
3. Reads the kernel's btime via `statx()` and compares with decoded on-disk values
4. Calls `_fix_exfat_raw()` with a known target datetime
5. Re-reads the cluster after modification and dumps a hex comparison
6. Decodes the on-disk fields independently of `statx()` to confirm the written byte values

The hex dump revealed:
```
Before: 06: 00 00  08: XX XX  0A: 5C AE  0C: XX XX
After:  06: 5C AE  08: XX XX  0A: 00     0C: XX XX
```

The `date_word` (`0x5CAE` = 2025-06-15) was at offset 0x06 (Reserved1) instead of 0x0A (CreateDate), and offset 0x0A held `0x00` (the `time_ms` value, one byte in a two-byte field).

### 3.4 Fix

The fix re-mapped the writes to the correct kernel field offsets:

```python
# Before (broken):
struct.pack_into('<H', entry, 0x06, date_word)  # WAS: writing to reserved1
struct.pack_into('<H', entry, 0x08, time_word)
entry[0x0A] = time_ms_val                        # WAS: overwriting CreateDate

# After (fixed):
struct.pack_into('<H', entry, 0x08, time_word)   # CreateTime
struct.pack_into('<H', entry, 0x0A, date_word)   # CreateDate
entry[0x14] = time_ms_val                         # CreateTimeMs
entry[0x15] = 0                                   # CreateTimezone = UTC
```

After the fix, the integration test confirms correct btime values across all tested datetimes.

---

## 4. Test Coverage

### 4.1 Unit Tests

**File**: `test/test_btime.py`

These test the pure-logic functions of `btime.py` without requiring block device access:

| Test | What it validates |
|------|-------------------|
| `test_resolve_method_auto_exfat` | `resolve_method('auto', 'exfat')` returns `'exfat_raw'` |
| `test_resolve_method_explicit` | `resolve_method('exfat_raw', 'exfat')` returns `'exfat_raw'` |
| `test_needs_processing_after` | `needs_processing_after('exfat_raw')` returns `True` |
| `test_fix_file_dry_run_clock` | `fix_file('exfat_raw', ...)` doesn't crash on dry run |

### 4.2 Integration Tests

**File**: `test/test_exfat_raw_btime.py`

These tests require:
- `udisksctl` (for loop device management)
- `sudo` (for block device I/O and cache flush)
- A compressed exFAT SD card image (`test/sdcard.img.gz`)

| Test | What it validates |
|------|-------------------|
| `test_set_btime_on_existing_file` | Reads btime before, writes a specific datetime, reads btime after. Asserts exact match of Unix timestamps. Validates the full read-write-cacheflush-read cycle. |
| `test_multiple_files_get_correct_btime` | Writes three different datetimes to three different files (2025-01-01, 2025-06-15, 2026-12-31). Each file's btime is individually verified. Tests that the strategy works with different time values including end-of-range. |
| `test_exfat_raw_is_registered_as_method` | Validates that `resolve_method` and `needs_processing_after` recognize `exfat_raw`. |

**FUSE+faketime comparison tests** in `test/test_fuse_faketime.py`:

| Test | What it validates |
|------|-------------------|
| `test_os_utime_does_not_change_existing_file_btime` | Creates a file under FUSE+faketime, then remounts with a different faketime offset and calls `os.utime()`. Asserts btime drift is **zero** — proving FUSE+faketime does not correct existing files. This test motivated the development of the raw exFAT strategy. |

### 4.3 Debug Helper

**File**: `test/debug_exfat.py`

A stand-alone debugging script (not a test) used during development to:

1. Set up a loop device
2. Parse the boot sector
3. Navigate the directory tree to a specific file
4. Dump the File Directory Entry as formatted hex bytes (two 16-byte rows)
5. Decode the creation time fields from raw bytes
6. Call `_fix_exfat_raw()` with a known target
7. Re-read the cluster and dump the hex comparison
8. Decode and print the after-write values

This script was instrumental in discovering the field offset bug (§3). It remains in the repository for future debugging of exFAT entry-related issues.

### 4.4 Test Infrastructure

**Test image**: A sparse exFAT SD card image containing GoPro test files with known timestamps. The image is:

- Compressed as `test/sdcard.img.gz` (~14 MB compressed, ~8.5 GB sparse)
- Decompressed on demand by each test class (cached in `test/sdcard.img`)
- Copied with `cp --sparse=always` to a per-test temp directory (each test gets its own writable copy)
- Mounted via `udisksctl loop-setup` + `udisksctl mount` (kernel exFAT driver)

**Cleanup**: Each test class cleans up loop devices and temp directories in `tearDownClass`.

**Success criteria**: btime is read via the `statx()` syscall (using `ctypes` to access `STATX_BTIME`). The test asserts **exact match** of the Unix timestamp (second precision), not a range or approximation. This is possible because exFAT btime has 10ms granularity and `statx()` reports second-level precision.

---

## 5. References

### exFAT Specification
- [Microsoft exFAT Specification (rev 1.00)](https://learn.microsoft.com/en-us/windows/win32/fileio/exfat-specification)
- Linux kernel exFAT header: [`fs/exfat/exfat.h`](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/fs/exfat/exfat.h) — defines `struct exfat_dentry` with field layout

### Implementation Files
- `src/btime.py` — all exFAT raw block code (lines 227–478)
- `test/test_exfat_raw_btime.py` — integration tests (184 lines)
- `test/debug_exfat.py` — debugging helper (135 lines)
- `test/test_fuse_faketime.py` — FUSE comparison test proving the need for raw block (431 lines)
- `test/test_btime.py` — unit tests for method registration (140 lines)
- `docs/btime-strategies/README.md` — strategy comparison document (562 lines)

### Related Kernel Work
- [Provenance Time (ptime) patch series](https://lwn.net/Articles/1066569/) — future kernel mechanism for setting creation time via standard syscalls
