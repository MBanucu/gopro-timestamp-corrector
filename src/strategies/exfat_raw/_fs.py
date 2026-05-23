"""Filesystem-level operations — FAT, clusters, directory traversal."""

import struct
from pathlib import Path

from strategies.exfat_raw._pure import _exfat_entry_name


class ExfatRawFilesystem:
    """FAT traversal and directory entry operations built on ``ExfatRawIO``."""

    def __init__(self, io):
        self._io = io

    # ── FAT ──────────────────────────────────────────────────────

    def read_fat(self, boot: dict, device: str, cluster: int) -> int:
        off = boot['fat_offset'] + cluster * 4
        data = self._io.read(device, off, 4)
        if len(data) < 4:
            return 0
        return struct.unpack_from('<I', data, 0)[0] & 0x0FFFFFFF

    def cluster_chain(self, boot: dict, device: str, start_cluster: int) -> list[int]:
        chain = []
        cl = start_cluster
        seen = set()
        while cl >= 2:
            if cl in seen:
                break
            seen.add(cl)
            chain.append(cl)
            nxt = self.read_fat(boot, device, cl)
            if nxt >= 0x0FFFFFF8:
                break
            cl = nxt
        return chain

    def read_clusters(self, boot: dict, device: str, chain: list[int]) -> list[bytes]:
        return [self._io.read(
            device,
            boot['cluster_heap_offset'] + (c - 2) * boot['cluster_size'],
            boot['cluster_size'])
            for c in chain]

    def write_clusters(self, boot: dict, device: str, chain: list[int], data: list[bytes]):
        for c, d in zip(chain, data):
            off = boot['cluster_heap_offset'] + (c - 2) * boot['cluster_size']
            self._io.write(device, off, d)

    # ── directory traversal ──────────────────────────────────────

    def collect_dir(self, boot: dict, device: str, dir_cluster: int) -> tuple[list[int], bytearray]:
        chain = self.cluster_chain(boot, device, dir_cluster)
        raw = self.read_clusters(boot, device, chain)
        buf = bytearray()
        for r in raw:
            buf.extend(r)
        return chain, buf

    def find_in_dir(self, boot: dict, device: str, dir_cluster: int, target_name: str):
        chain, buf = self.collect_dir(boot, device, dir_cluster)
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

    # ── path resolution & file entry lookup ──────────────────────

    def _resolve_path(self, filepath: str) -> tuple[str, str, list[str], str] | None:
        from btime import _resolve_device, _resolve_mount_point
        device = _resolve_device(filepath)
        if not device:
            return None
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
        return device, mount_point, parts, filename

    def find_file_entry(self, boot: dict, device: str, filepath: str) -> bytes | None:
        resolved = self._resolve_path(filepath)
        if not resolved:
            return None
        dev, _mp, parts, filename = resolved
        current_cluster = boot['root_cluster']
        for component in parts:
            found = self.find_in_dir(boot, dev, current_cluster, component)
            if not found:
                return None
            _chain, _ci, _off, _sc, dentries = found
            stream = dentries[1]
            first_cl = struct.unpack_from('<I', stream, 0x14)[0]
            current_cluster = first_cl
        found = self.find_in_dir(boot, dev, current_cluster, filename)
        if not found:
            return None
        return found[4][0]
