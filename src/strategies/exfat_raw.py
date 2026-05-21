import os
import struct
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from strategies.base import BtimeStrategy


def _exfat_crc16(data: bytes, crc: int = 0) -> int:
    for byte in data:
        crc ^= byte << 8
    for _ in range(8):
        crc = (crc << 1) ^ 0x1021 if crc & 0x8000 else crc << 1
    crc &= 0xFFFF
    return crc


def _exfat_entry_set_crc(entries: list[bytes]) -> int:
    crc = 0
    for entry in entries:
        crc = _exfat_crc16(entry[:2], crc)
        crc = _exfat_crc16(b'\x00\x00', crc)
        crc = _exfat_crc16(entry[4:], crc)
    return crc


def _exfat_encode_time(dt):
    utc = dt.replace(tzinfo=timezone.utc)
    year, month, day = utc.year, utc.month, utc.day
    hour, minute = utc.hour, utc.minute
    total_sec = int(utc.timestamp())
    sec = total_sec % 60
    ms = utc.microsecond // 1000
    date_word = ((year - 1980) << 9) | (month << 5) | day
    time_word = (hour << 11) | (minute << 5) | (sec // 2)
    time_ms = (sec % 2) * 100 + (ms // 10)
    return date_word, time_word, time_ms


def _exfat_decode_time(time_word: int, date_word: int, time_ms: int) -> datetime:
    year = ((date_word >> 9) & 0x7F) + 1980
    month = (date_word >> 5) & 0x0F
    day = date_word & 0x1F
    hour = (time_word >> 11) & 0x1F
    minute = (time_word >> 5) & 0x3F
    sec_2block = time_word & 0x1F
    odd_second = 1 if time_ms >= 100 else 0
    second = sec_2block * 2 + odd_second
    millisecond = (time_ms % 100) * 10
    return datetime(year, month, day, hour, minute, second,
                    millisecond * 1000, tzinfo=timezone.utc)


def _exfat_read_device(device: str, offset: int, size: int) -> bytes:
    r = subprocess.run(
        ['sudo', 'dd', f'if={device}', 'bs=1', f'skip={offset}',
         f'count={size}', 'status=none', 'iflag=nocache'],
        capture_output=True)
    if r.returncode != 0 or len(r.stdout) != size:
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
    off = boot['fat_offset'] + cluster * 4
    data = _exfat_read_device(device, off, 4)
    if len(data) < 4:
        return 0
    return struct.unpack_from('<I', data, 0)[0] & 0x0FFFFFFF


def _exfat_cluster_chain(boot: dict, device: str, start_cluster: int) -> list[int]:
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
    chain = _exfat_cluster_chain(boot, device, dir_cluster)
    raw = _exfat_read_clusters(boot, device, chain)
    buf = bytearray()
    for r in raw:
        buf.extend(r)
    return chain, buf


def _exfat_entry_name(entries: list[bytes]) -> str:
    chars = []
    for e in entries:
        if e[0] == 0xC1:
            raw = e[2:32]
            for pos in range(0, 30, 2):
                cp = struct.unpack_from('<H', raw, pos)[0]
                if cp == 0:
                    break
                chars.append(chr(cp))
    return ''.join(chars)


def _exfat_find_in_dir(boot: dict, device: str, dir_cluster: int, target_name: str):
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


class ExfatRawStrategy(BtimeStrategy):
    name = 'exfat_raw'
    label = 'exFAT raw block'

    @classmethod
    def compatible_filesystems(cls) -> tuple[str, ...]:
        return ('exfat', 'vfat', 'msdos', 'fuseblk')

    @classmethod
    def required_tools(cls) -> tuple[str, ...]:
        return ('dd', 'findmnt', 'sudo', 'sync', 'mount', 'umount')

    @classmethod
    def needs_teardown(cls) -> bool:
        return True

    @classmethod
    def handles_mtime(cls) -> bool:
        return True

    def setup(self, target_path, delta, dry_run):
        return {}

    def fix_file(self, filepath, dt, ctx, dry_run):
        import sys as _sys
        _fix_exfat_raw(filepath, dt, dry_run)

    def teardown(self, ctx, dry_run):
        pass


def _exfat_find_file_entry(boot: dict, device: str, filepath: str) -> bytes | None:
    """Find the file-directory entry for *filepath* on an exFAT volume.

    Returns the raw 32‑byte file‑directory entry, or ``None``.
    """
    from btime import _resolve_device, _resolve_mount_point

    mount_point = _resolve_mount_point(filepath)
    if not mount_point:
        return None
    fp = Path(filepath).resolve()
    mp = Path(mount_point).resolve()
    try:
        rel = fp.relative_to(mp)
    except ValueError:
        return None
    parts = list(rel.parts)
    filename = parts.pop()

    current_cluster = boot['root_cluster']
    for component in parts:
        found = _exfat_find_in_dir(boot, device, current_cluster, component)
        if not found:
            return None
        dchain, dci, doff, dsc, dentries = found
        stream = dentries[1]
        first_cl = struct.unpack_from('<I', stream, 0x14)[0]
        current_cluster = first_cl

    found = _exfat_find_in_dir(boot, device, current_cluster, filename)
    if not found:
        return None
    return found[4][0]  # first entry of the entry set


def read_exfat_btime_raw(filepath: str) -> int | None:
    """Read birth time from an exFAT filesystem via raw block access.

    Returns epoch seconds (UTC) or ``None`` on failure.
    Unlike ``stat -c '%W'`` this works on **all** kernel versions
    because it reads the on‑disk directory entry directly.
    """
    from btime import _resolve_device

    try:
        device = _resolve_device(filepath)
    except OSError:
        return None
    if not device:
        return None

    boot = _exfat_parse_boot(device)
    if not boot:
        return None

    entry = _exfat_find_file_entry(boot, device, filepath)
    if entry is None:
        return None

    time_word = struct.unpack_from('<H', entry, 0x0C)[0]
    date_word = struct.unpack_from('<H', entry, 0x0E)[0]
    time_ms = entry[0x16]

    dt = _exfat_decode_time(time_word, date_word, time_ms)
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def read_exfat_mtime_raw(filepath: str) -> int | None:
    """Read modification time from an exFAT file via raw block access.

    Returns epoch seconds (UTC) or ``None`` on failure.
    Unlike ``os.path.getmtime()`` this bypasses the kernel cache
    and reads directly from the on‑disk directory entry.
    """
    from btime import _resolve_device
    try:
        device = _resolve_device(filepath)
    except OSError:
        return None
    if not device:
        return None
    boot = _exfat_parse_boot(device)
    if not boot:
        return None
    entry = _exfat_find_file_entry(boot, device, filepath)
    if entry is None:
        return None
    time_word = struct.unpack_from('<H', entry, 0x08)[0]
    date_word = struct.unpack_from('<H', entry, 0x0A)[0]
    time_ms = entry[0x14]
    dt = _exfat_decode_time(time_word, date_word, time_ms)
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def _fix_exfat_raw(filepath, dt, dry_run, btime_dt=None):
    """Write both modification time and creation time to an exFAT file.

    *dt* is the target modification time (written to offsets 0x08/0x0A/0x14).
    When *btime_dt* is ``None`` (default), the creation time (offsets
    0x0C/0x0E/0x16) is set to the same value.  When *btime_dt* is
    provided, the creation time is preserved at that value (useful when
    writing mtime-only after btime was already corrected).
    """
    from btime import _resolve_device, _resolve_mount_point

    device = _resolve_device(filepath)
    if not device:
        raise RuntimeError("Could not resolve block device for file")

    boot = _exfat_parse_boot(device)
    if not boot:
        raise RuntimeError("Could not parse exFAT boot sector on " + device)

    utc = dt.replace(tzinfo=timezone.utc)
    label = utc.strftime("%Y-%m-%d %H:%M:%S")
    btime_utc = btime_dt.replace(tzinfo=timezone.utc) if btime_dt is not None else utc
    import sys as _sys
    _sys.stderr.write(f"[dbg] _fix_exfat_raw dt={dt!r} utc_timestamp={int(utc.timestamp())} device={device}\n")

    if dry_run:
        print(f"    Would set btime via exFAT raw block write to {label} UTC")
        return

    subprocess.run(['sync'])

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

    filename = parts.pop()

    current_cluster = boot['root_cluster']
    for component in parts:
        found = _exfat_find_in_dir(boot, device, current_cluster, component)
        if not found:
            raise RuntimeError(
                f"exFAT: directory component '{component}' not found "
                f"(cluster={current_cluster}, device={device})")
        dchain, dci, doff, dsc, dentries = found
        stream = dentries[1]
        first_cl = struct.unpack_from('<I', stream, 0x14)[0]
        current_cluster = first_cl

    found = _exfat_find_in_dir(boot, device, current_cluster, filename)
    if not found:
        raise RuntimeError(f"exFAT: file '{filename}' not found in directory")
    fchain, fci, foff, fsc, fentries = found

    entry = bytearray(fentries[0])
    mdate_word, mtime_word, mtime_ms_val = _exfat_encode_time(utc)
    bdate_word, btime_word, btime_ms_val = _exfat_encode_time(btime_utc)
    struct.pack_into('<H', entry, 0x08, mtime_word)
    struct.pack_into('<H', entry, 0x0A, mdate_word)
    entry[0x14] = mtime_ms_val
    entry[0x15] = 0
    struct.pack_into('<H', entry, 0x0C, btime_word)
    struct.pack_into('<H', entry, 0x0E, bdate_word)
    entry[0x16] = btime_ms_val
    entry[0x17] = 0

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

    subprocess.run(['sync'])
    subprocess.run(['sudo', 'sh', '-c', 'echo 3 > /proc/sys/vm/drop_caches'],
                   capture_output=True)
    print(f"    \u2713  btime corrected via exFAT raw block write ({label} UTC)")
