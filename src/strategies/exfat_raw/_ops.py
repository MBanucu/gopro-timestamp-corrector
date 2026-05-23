"""High-level exFAT raw-block operations — read/write btime, mtime, correction."""

import os
import struct
import subprocess
from datetime import datetime, timezone

from strategies.exfat_raw._pure import _exfat_encode_time, _exfat_decode_time, _exfat_entry_set_crc


class ExfatRawOps:
    """High-level operations using ``ExfatRawIO`` + ``ExfatRawFilesystem``."""

    def __init__(self, io, fs):
        self._io = io
        self._fs = fs

    # ── raw-block time reads ─────────────────────────────────────

    def read_btime_raw(self, filepath: str) -> int | None:
        from btime import _resolve_device
        try:
            device = _resolve_device(filepath)
        except OSError:
            return None
        if not device:
            return None
        boot = self._io.parse_boot(device)
        if not boot:
            return None
        entry = self._fs.find_file_entry(boot, device, filepath)
        if entry is None:
            return None
        tw = struct.unpack_from('<H', entry, 0x0C)[0]
        dw = struct.unpack_from('<H', entry, 0x0E)[0]
        tms = entry[0x16]
        dt = _exfat_decode_time(tw, dw, tms)
        return int(dt.replace(tzinfo=timezone.utc).timestamp())

    def read_mtime_raw(self, filepath: str) -> int | None:
        from btime import _resolve_device
        try:
            device = _resolve_device(filepath)
        except OSError:
            return None
        if not device:
            return None
        boot = self._io.parse_boot(device)
        if not boot:
            return None
        entry = self._fs.find_file_entry(boot, device, filepath)
        if entry is None:
            return None
        tw = struct.unpack_from('<H', entry, 0x08)[0]
        dw = struct.unpack_from('<H', entry, 0x0A)[0]
        tms = entry[0x14]
        dt = _exfat_decode_time(tw, dw, tms)
        return int(dt.replace(tzinfo=timezone.utc).timestamp())

    # ── core fix operation ───────────────────────────────────────

    def fix_exfat_raw(self, filepath: str, dt: datetime, dry_run: bool,
                       btime_dt: datetime | None = None,
                       update_cache: bool = True):
        from btime import _resolve_device

        device = _resolve_device(filepath)
        if not device:
            raise RuntimeError("Could not resolve block device for file")

        boot = self._io.parse_boot(device)
        if not boot:
            raise RuntimeError("Could not parse exFAT boot sector on " + device)

        utc = dt.replace(tzinfo=timezone.utc)
        label = utc.strftime("%Y-%m-%d %H:%M:%S")
        btime_utc = btime_dt.replace(tzinfo=timezone.utc) if btime_dt is not None else utc

        if dry_run:
            print(f"    Would set btime via exFAT raw block write to {label} UTC")
            return

        resolved = self._fs._resolve_path(filepath)
        if not resolved:
            raise RuntimeError(f"Could not resolve path {filepath}")
        _dev, _mp, parts, filename = resolved

        current_cluster = boot['root_cluster']
        for component in parts:
            found = self._fs.find_in_dir(boot, device, current_cluster, component)
            if not found:
                raise RuntimeError(
                    f"exFAT: directory component '{component}' not found "
                    f"(cluster={current_cluster}, device={device})")
            _chain, _ci, _off, _sc, dentries = found
            stream = dentries[1]
            first_cl = struct.unpack_from('<I', stream, 0x14)[0]
            current_cluster = first_cl

        found = self._fs.find_in_dir(boot, device, current_cluster, filename)
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
        cluster_data = self._fs.read_clusters(boot, device, [fchain[fci]])[0]
        cluster_buf = bytearray(cluster_data)
        off = foff
        for e in modified_entries:
            cluster_buf[off:off + 32] = e
            off += 32
        self._fs.write_clusters(boot, device, [fchain[fci]], [bytes(cluster_buf)])

        # fsync on the backing file (inside _io.write) already persists data.
        # No global sync() here — it triggers exFAT driver writeback that can
        # flush dirty inodes from ANOTHER mount to THIS mount's directory
        # entry (kernel 6.12.87 cross-mount DE corruption bug).

        if not dry_run and update_cache:
            pass  # os.utime omitted — kernel exFAT driver DE cache is
                  # incoherent after raw-block write and cannot be flushed
                  # from userspace; utime would read stale btime and
                  # overwrite the raw-block changes.

        print(f"    \u2713  btime corrected via exFAT raw block write ({label} UTC)")
