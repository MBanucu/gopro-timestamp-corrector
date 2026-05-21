import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from datetime import timezone
from pathlib import Path

from options import (BTIME_AUTO, BTIME_CLOCK, BTIME_DEBUGFS,
                     BTIME_FUSE, BTIME_EXFAT_RAW)


def _resolve_device(path):
    st = os.stat(path)
    major = os.major(st.st_dev)
    minor = os.minor(st.st_dev)
    with open('/proc/partitions') as f:
        for line in f:
            parts = line.split()
            if len(parts) == 4 and parts[0].isdigit():
                if int(parts[0]) == major and int(parts[1]) == minor:
                    return f'/dev/{parts[3]}'
    try:
        link = os.readlink(f'/sys/dev/block/{major}:{minor}')
        return os.path.join('/dev', os.path.basename(link))
    except OSError:
        return None


def detect_fs(path):
    try:
        result = subprocess.run(
            ['df', '--output=fstype', str(path)],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            lines = result.stdout.strip().splitlines()
            if len(lines) >= 2:
                fs = lines[1].strip()
                if fs:
                    return 'exfat' if fs == 'fuseblk' else fs
    except (FileNotFoundError, OSError):
        pass
    return _detect_fs_from_mounts(path)


def _detect_fs_from_mounts(path):
    """Fallback: parse /proc/mounts when df is unavailable."""
    try:
        with open('/proc/mounts') as f:
            mounts = [(ln.split()[0], ln.split()[1], ln.split()[2])
                      for ln in f if len(ln.split()) >= 3]
    except OSError:
        return None
    path_str = str(path)
    best = (None, 0)
    for dev, mp, fs in mounts:
        if path_str.startswith(mp) and len(mp) > best[1]:
            best = ('exfat' if fs == 'fuseblk' else fs, len(mp))
    return best[0]


def resolve_method(requested, fs_type):
    if requested == 'debugfs':
        return 'debugfs'
    elif requested == 'fuse':
        return 'fuse'
    elif requested == 'exfat_raw':
        return 'exfat_raw'
    elif requested == 'clock':
        return 'clock'
    if fs_type == 'ext4':
        return 'debugfs'
    elif fs_type == 'exfat':
        return 'exfat_raw'
    elif fs_type in ('vfat', 'msdos'):
        return 'fuse'
    return 'clock'


def chain_setup(methods, target_path, fs_type, delta, dry_run):
    """Try *methods* in order, returning the first one whose setup succeeds.

    ``'auto'`` is expanded to the full optimal method order for *fs_type*
    (e.g. on exFAT: ``['exfat_raw', 'fuse', 'clock']``).

    Args:
        methods: iterable of method names (str).  ``'auto'`` is expanded
                 to the full compatible chain; explicit names are used as-is.
        target_path: Path for filesystem detection.
        fs_type: pre‑detected filesystem type.
        delta: timedelta offset for setup.
        dry_run: True to only simulate.

    Returns:
        (method, ctx) tuple where *method* is the resolved concrete name
        (e.g. ``'exfat_raw'``) and *ctx* the setup context dict (may be {}).
        Returns (None, {}) when every method fails.
    """
    expanded = []
    for m in methods:
        if m == BTIME_AUTO:
            expanded.extend(
                cm for cm in compatible_methods(fs_type) if cm != BTIME_AUTO
            )
        else:
            expanded.append(m)

    for method in expanded:
        resolved = resolve_method(method, fs_type)
        if needs_processing_before(resolved):
            ctx = setup(resolved, target_path, delta, dry_run) or {}
            if not ctx and resolved == 'fuse':
                continue
            return resolved, ctx
        if resolved == 'clock':
            ctx = setup(resolved, target_path, delta, dry_run) or {}
            return resolved, ctx
        if resolved == 'exfat_raw':
            if os.path.exists(target_path) and _resolve_device(target_path) is None:
                continue
        return resolved, {}
    return None, {}


def compatible_methods(fs_type):
    """Return the btime method names applicable to *fs_type*.

    ``'clock'`` is always included as the universal last‑resort.
    Methods that cannot possibly work on the given filesystem are
    excluded so the UI can filter them out.

    Note: ``'auto'`` is **not** returned — it is a purely backend
    concept handled by :func:`chain_setup`.  Callers that want the
    expanded concrete list for a known filesystem should use this
    function directly.

    Args:
        fs_type: filesystem type as returned by ``detect_fs()``
                 (e.g. ``'ext4'``, ``'exfat'``, ``'vfat'``, ``'fuseblk'``).

    Returns:
        tuple of method name strings in a sensible default order.
    """
    methods = []
    if fs_type and fs_type.startswith('ext'):
        methods.append(BTIME_DEBUGFS)
    elif fs_type in ('exfat', 'vfat', 'msdos', 'fuseblk'):
        methods.append(BTIME_EXFAT_RAW)
        methods.append(BTIME_FUSE)
    methods.append(BTIME_CLOCK)
    return tuple(methods)


def needs_processing_before(method):
    return method == 'fuse'


def needs_processing_after(method):
    return method in ('debugfs', 'exfat_raw')


def setup(method, target_path, delta, dry_run):
    if method == 'fuse':
        return _setup_fuse(target_path, delta, dry_run)
    elif method == 'clock':
        return _setup_clock(dry_run)
    return {}


def teardown(method, ctx, dry_run):
    if method == 'fuse':
        _teardown_fuse(ctx, dry_run)
    elif method == 'clock':
        _teardown_clock(ctx, dry_run)


def fix_file(method, filepath, dt, ctx, dry_run):
    if method == 'debugfs':
        _fix_debugfs(filepath, dt, dry_run)
    elif method == 'exfat_raw':
        _fix_exfat_raw(filepath, dt, dry_run)
    elif method == 'clock':
        _fix_clock_set_time(dt, dry_run)


def _fix_debugfs(filepath, dt, dry_run):
    st = os.stat(filepath)
    device = _resolve_device(filepath)
    if not device:
        print(f"    ! Could not resolve device")
        return

    ts_sec = int(dt.replace(tzinfo=timezone.utc).timestamp())

    if dry_run:
        print(f"    Would set btime via debugfs on inode {st.st_ino}")
        return

    r1 = subprocess.run(['sudo', 'debugfs', '-w', device, '-R',
                         f'set_inode_field <{st.st_ino}> crtime_lo {ts_sec}'],
                        capture_output=True, text=True)
    r2 = subprocess.run(['sudo', 'debugfs', '-w', device, '-R',
                         f'set_inode_field <{st.st_ino}> crtime_extra 0'],
                        capture_output=True, text=True)

    if r1.returncode != 0:
        print(f"    \u2717  debugfs failed: {r1.stderr.strip()}")
        return

    subprocess.run(['sync'])
    subprocess.run(['sudo', 'sh', '-c', 'echo 3 > /proc/sys/vm/drop_caches'],
                   capture_output=True)
    print(f"    \u2713  btime corrected via debugfs")


def _resolve_mount_point(path):
    """Resolve the mount point for *path* using findmnt."""
    r = subprocess.run(
        ['findmnt', '-n', '-o', 'TARGET', '--target', str(path)],
        capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    return None


def _setup_fuse(target_path, delta, dry_run):
    if not shutil.which('faketime'):
        print("  ! faketime not found. Install libfaketime or use --fix-btime clock.")
        return None
    if not shutil.which('mount.exfat-fuse'):
        print("  ! mount.exfat-fuse not found. Install exfat or use --fix-btime clock.")
        return None

    if not os.path.exists(target_path):
        print("  ! Path does not exist. Falling back to clock method.")
        return None

    device = _resolve_device(target_path)
    if not device:
        print("  ! Could not resolve device. Falling back to clock method.")
        return None

    mount_point = _resolve_mount_point(target_path)
    if not mount_point:
        print(f"  ! Could not resolve mount point for {target_path}.")
        return None

    total_sec = int(delta.total_seconds())
    offset = f'+{total_sec}' if total_sec >= 0 else str(total_sec)

    if dry_run:
        print(f"    Would unmount {mount_point} and remount with FUSE + faketime")
        return {'device': device, 'offset': offset}

    result = subprocess.run(
        ['sudo', 'umount', mount_point],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        result = subprocess.run(
            ['sudo', 'umount', '-l', mount_point],
            capture_output=True, text=True
        )
    if result.returncode != 0:
        print(f"    ! Failed to unmount: {result.stderr.strip()}")
        return None

    subprocess.run(['sudo', 'mkdir', '-p', mount_point], capture_output=True)

    uid = os.getuid()
    gid = os.getgid()

    proc = subprocess.Popen(
        ['sudo', 'faketime', '-f', offset, 'mount.exfat-fuse', device, mount_point,
         '-o', f'uid={uid}', '-o', f'gid={gid}',
         '-o', 'allow_other', '-o', 'nonempty', '-o', 'auto_unmount'],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
    )

    for _ in range(5000):
        if proc.poll() is not None:
            err = proc.stderr.read().strip() if proc.stderr else ''
            print(f"    ! FUSE mount failed: {err}")
            subprocess.run(['sudo', 'mkdir', '-p', mount_point], capture_output=True)
            r = subprocess.run(['sudo', 'mount', device, mount_point], capture_output=True, text=True)
            if r.returncode != 0:
                print(f"    ! Failed to remount kernel: {r.stderr.strip()}")
            return None
        if os.path.ismount(mount_point):
            break
        time.sleep(0.002)
    else:
        err = proc.stderr.read().strip() if proc.stderr else ''
        print(f"    ! FUSE mount timed out: {err}")
        subprocess.run(['sudo', 'mkdir', '-p', mount_point], capture_output=True)
        r = subprocess.run(['sudo', 'mount', device, mount_point], capture_output=True, text=True)
        if r.returncode != 0:
            print(f"    ! Failed to remount kernel: {r.stderr.strip()}")
        return None

    print(f"    \u2713  FUSE + faketime mounted ({delta})")
    if proc.stderr:
        proc.stderr.close()
    return {'proc': proc, 'mount': mount_point, 'device': device}


def _teardown_fuse(ctx, dry_run):
    if dry_run or not ctx:
        return
    mount = ctx.get('mount')
    proc = ctx.get('proc')
    if proc:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    if mount:
        r = subprocess.run(['sudo', 'umount', '-f', str(mount)], capture_output=True)
        if r.returncode != 0:
            subprocess.run(['sudo', 'umount', '-l', str(mount)], capture_output=True)
        device = ctx.get('device')
        if device:
            subprocess.run(['sudo', 'mkdir', '-p', mount], capture_output=True)
            subprocess.run(['sudo', 'mount', device, mount], capture_output=True)
    print(f"    FUSE mount torn down.")


# ── exFAT raw block manipulation ──────────────────────────────────

def _exfat_crc16(data: bytes, crc: int = 0) -> int:
    """CRC-16/CCITT with polynomial 0x1021, initial value 0."""
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = (crc << 1) ^ 0x1021 if crc & 0x8000 else crc << 1
        crc &= 0xFFFF
    return crc


def _exfat_entry_set_crc(entries: list[bytes]) -> int:
    """CRC-16 over an entry set, zeroing the set_checksum field (bytes 2-3) of each 32-byte entry."""
    crc = 0
    for entry in entries:
        crc = _exfat_crc16(entry[:2], crc)
        crc = _exfat_crc16(b'\x00\x00', crc)
        crc = _exfat_crc16(entry[4:], crc)
    return crc


def _exfat_encode_time(dt):
    """Encode a UTC datetime into exFAT date/time/ms fields (little-endian uint16+byte)."""
    utc = dt.replace(tzinfo=timezone.utc)
    year, month, day = utc.year, utc.month, utc.day
    hour, minute = utc.hour, utc.minute
    total_sec = int(utc.timestamp())
    sec = total_sec % 60
    ms = utc.microsecond // 1000
    date_word = ((year - 1980) << 9) | (month << 5) | day
    time_word = (hour << 11) | (minute << 5) | (sec // 2)
    time_ms = (sec % 2) * 100 + (ms // 10)
    return date_word, time_word, time_ms  # each packed as uint16, uint16, uint8


def _exfat_read_device(device: str, offset: int, size: int) -> bytes:
    r = subprocess.run(
        ['sudo', 'dd', f'if={device}', 'bs=1', f'skip={offset}',
         f'count={size}', 'status=none'],
        capture_output=True)
    return r.stdout


def _exfat_write_device(device: str, offset: int, data: bytes):
    with tempfile.NamedTemporaryFile() as tf:
        tf.write(data)
        tf.flush()
        subprocess.run(
            ['sudo', 'dd', f'if={tf.name}', f'of={device}',
             'bs=1', f'seek={offset}', f'count={len(data)}',
             'status=none'],
            check=True, capture_output=True)


def _exfat_parse_boot(device: str):
    data = _exfat_read_device(device, 0, 512)
    if len(data) < 512:
        return None
    sig = struct.unpack_from('<H', data, 510)[0]
    if sig != 0xAA55:
        return None
    bps = 1 << data[0x6C]
    spc = 1 << data[0x6D]
    return {
        'bytes_per_sector': bps,
        'sec_per_cluster': spc,
        'cluster_size': bps * spc,
        'fat_offset': struct.unpack_from('<I', data, 0x50)[0] * bps,
        'cluster_heap_offset': struct.unpack_from('<I', data, 0x58)[0] * bps,
        'root_cluster': struct.unpack_from('<I', data, 0x60)[0],
    }


def _exfat_read_fat(boot: dict, device: str, cluster: int) -> int:
    """Return the FAT value for *cluster* (next cluster or end-of-chain marker)."""
    off = boot['fat_offset'] + cluster * 4
    data = _exfat_read_device(device, off, 4)
    if len(data) < 4:
        return 0
    return struct.unpack_from('<I', data, 0)[0] & 0x0FFFFFFF


def _exfat_cluster_chain(boot: dict, device: str, start_cluster: int) -> list[int]:
    """Return ordered list of cluster indices in the chain starting at *start_cluster*."""
    chain = []
    cl = start_cluster
    seen = set()
    while cl >= 2:
        if cl in seen:
            break
        seen.add(cl)
        chain.append(cl)
        nxt = _exfat_read_fat(boot, device, cl)
        if nxt >= 0x0FFFFFF8:
            break
        cl = nxt
    return chain


def _exfat_read_clusters(boot: dict, device: str, chain: list[int]) -> list[bytes]:
    return [_exfat_read_device(device, boot['cluster_heap_offset'] + (c - 2) * boot['cluster_size'],
                               boot['cluster_size']) for c in chain]


def _exfat_write_clusters(boot: dict, device: str, chain: list[int], data: list[bytes]):
    for c, d in zip(chain, data):
        off = boot['cluster_heap_offset'] + (c - 2) * boot['cluster_size']
        _exfat_write_device(device, off, d)


def _exfat_collect_dir(boot: dict, device: str, dir_cluster: int) -> tuple[list[int], bytearray]:
    """Read a directory cluster chain into a single contiguous bytearray. Returns (chain, data)."""
    chain = _exfat_cluster_chain(boot, device, dir_cluster)
    raw = _exfat_read_clusters(boot, device, chain)
    buf = bytearray()
    for r in raw:
        buf.extend(r)
    return chain, buf


def _exfat_entry_name(entries: list[bytes]) -> str:
    """Reconstruct the filename from name entries (type 0xC1)."""
    chars = []
    for e in entries:
        if e[0] == 0xC1:
            raw = e[2:32]  # 30 bytes = 15 UTF-16LE chars
            for pos in range(0, 30, 2):
                cp = struct.unpack_from('<H', raw, pos)[0]
                if cp == 0:
                    break
                chars.append(chr(cp))
    return ''.join(chars)


def _exfat_find_in_dir(boot: dict, device: str, dir_cluster: int, target_name: str):
    """Scan a directory for a subdirectory/file named *target_name*.

    Returns (chain, cluster_index_in_chain, offset_of_file_entry, secondary_count, entry_set_bytes)
    or None if not found.
    """
    chain, buf = _exfat_collect_dir(boot, device, dir_cluster)
    cs = boot['cluster_size']
    pos = 0
    while pos + 32 <= len(buf):
        entry_type = buf[pos]
        if entry_type == 0x00:
            break
        if entry_type == 0x85:
            sc = buf[pos + 1]
            total_entries = 1 + sc
            set_bytes = total_entries * 32
            if pos + set_bytes > len(buf):
                break
            entries = [bytes(buf[pos + i * 32: pos + (i + 1) * 32]) for i in range(total_entries)]
            name = _exfat_entry_name(entries[2:])
            if name == target_name:
                cluster_in_chain = pos // cs
                offset_in_cluster = pos % cs
                return chain, cluster_in_chain, offset_in_cluster, sc, entries
        pos += 32
    return None


def _fix_exfat_raw(filepath, dt, dry_run):
    device = _resolve_device(filepath)
    if not device:
        raise RuntimeError("Could not resolve block device for file")

    boot = _exfat_parse_boot(device)
    if not boot:
        raise RuntimeError("Could not parse exFAT boot sector on " + device)

    utc = dt.replace(tzinfo=timezone.utc)
    label = utc.strftime("%Y-%m-%d %H:%M:%S")

    if dry_run:
        print(f"    Would set btime via exFAT raw block write to {label} UTC")
        return

    # Sync before touching the raw device: the kernel may have pending
    # dirty entries from exiftool's embedded write that must be flushed
    # to disk first.  Without this the cluster read gets stale data and
    # the subsequent cluster write‑back gets overwritten by the flusher.
    subprocess.run(['sync'])

    # Walk the directory tree from mount point to file
    mount_point = _resolve_mount_point(filepath)
    if not mount_point:
        raise RuntimeError("Could not resolve mount point for " + filepath)
    fp = Path(filepath).resolve()
    mp = Path(mount_point).resolve()
    try:
        rel = fp.relative_to(mp)
    except ValueError:
        raise RuntimeError(f"File {fp} is not under mount point {mount_point}")
    parts = list(rel.parts)

    # The last part is the filename — remove it from traversal, handle separately
    filename = parts.pop()

    current_cluster = boot['root_cluster']
    for component in parts:
        found = _exfat_find_in_dir(boot, device, current_cluster, component)
        if not found:
            raise RuntimeError(f"exFAT: directory component '{component}' not found")
        dchain, dci, doff, dsc, dentries = found
        stream = dentries[1]
        first_cl = struct.unpack_from('<I', stream, 0x14)[0]
        current_cluster = first_cl

    # Find the file's entry set
    found = _exfat_find_in_dir(boot, device, current_cluster, filename)
    if not found:
        raise RuntimeError(f"exFAT: file '{filename}' not found in directory")
    fchain, fci, foff, fsc, fentries = found

    # Found the file entry set — modify creation time and modification time
    entry = bytearray(fentries[0])
    date_word, time_word, time_ms_val = _exfat_encode_time(utc)
    struct.pack_into('<H', entry, 0x08, time_word)
    struct.pack_into('<H', entry, 0x0A, date_word)
    entry[0x14] = time_ms_val
    entry[0x15] = 0  # timezone = UTC
    struct.pack_into('<H', entry, 0x0C, time_word)
    struct.pack_into('<H', entry, 0x0E, date_word)
    entry[0x16] = time_ms_val
    entry[0x17] = 0  # timezone = UTC

    # Update the set checksum (written to the first entry; kernel only checks first entry)
    modified_entries = [bytes(entry)] + list(fentries[1:])
    crc = _exfat_entry_set_crc(modified_entries)
    struct.pack_into('<H', entry, 2, crc)
    modified_entries[0] = bytes(entry)

    cs = boot['cluster_size']
    cluster_data = _exfat_read_clusters(boot, device, [fchain[fci]])[0]
    cluster_buf = bytearray(cluster_data)
    off = foff
    for e in modified_entries:
        cluster_buf[off:off + 32] = e
        off += 32
    _exfat_write_clusters(boot, device, [fchain[fci]], [bytes(cluster_buf)])

    # Flush caches so the kernel picks up the changes
    subprocess.run(['sync'])
    subprocess.run(['sudo', 'sh', '-c', 'echo 3 > /proc/sys/vm/drop_caches'],
                   capture_output=True)
    print(f"    \u2713  btime corrected via exFAT raw block write ({label} UTC)")


def _setup_clock(dry_run):
    if dry_run:
        print(f"    Would stop NTP (timedatectl set-ntp false)")
        return {'ntp_stopped': True}
    result = subprocess.run(
        ['sudo', 'timedatectl', 'set-ntp', 'false'],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"    ! Failed to stop NTP: {result.stderr.strip()}")
        return {}
    return {'ntp_stopped': True}


def _teardown_clock(ctx, dry_run):
    if dry_run or not ctx:
        return
    if ctx.get('ntp_stopped'):
        result = subprocess.run(
            ['sudo', 'timedatectl', 'set-ntp', 'true'],
            capture_output=True
        )
        if result.returncode == 0:
            print(f"    NTP restarted, clock syncing...")
        else:
            for cmd in [
                ['systemd-run', '--user', '--on-calendar', 'now', 'systemd-timesyncd'],
                ['sudo', 'ntpdate', '-u', 'pool.ntp.org'],
            ]:
                r = subprocess.run(cmd, capture_output=True, timeout=10)
                if r.returncode == 0:
                    break


def _fix_clock_set_time(dt, dry_run):
    ts = int(dt.replace(tzinfo=timezone.utc).timestamp())
    label = dt.strftime("%Y-%m-%d %H:%M:%S")
    if dry_run:
        print(f"    Would set clock to: {label}")
        return
    result = subprocess.run(
        ['sudo', 'date', '-s', f'@{ts}'],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f"    Clock set to: {label}")
    else:
        print(f"    ! Failed to set clock: {result.stderr.strip()}")
