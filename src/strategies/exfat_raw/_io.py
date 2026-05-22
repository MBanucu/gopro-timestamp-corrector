"""Low-level raw block I/O for exFAT filesystems."""

import os
import struct
import subprocess
import tempfile


class ExfatRawIO:
    """Raw block I/O with per-instance backing-file cache.

    Each instance has its own backing-file cache, making it safe to
    use in tests without cross-contamination.  The canonical singleton
    is ``exfat_io`` in :mod:`strategies.exfat_raw`.
    """

    def __init__(self):
        self._backing_cache: dict[str, str | None] = {}

    # ── internal: backing-file resolution ────────────────────────

    def _backing_file(self, device: str) -> str | None:
        if device not in self._backing_cache:
            dev_name = device.lstrip('/dev/')
            for cmd in (
                ['cat', f'/sys/block/{dev_name}/loop/backing_file'],
                ['sudo', 'cat', f'/sys/block/{dev_name}/loop/backing_file'],
                ['losetup', '-n', '-O', 'BACK-FILE', device],
                ['sudo', 'losetup', '-n', '-O', 'BACK-FILE', device],
            ):
                try:
                    r = subprocess.run(
                        cmd, capture_output=True, text=True, timeout=5)
                    if r.returncode == 0:
                        self._backing_cache[device] = r.stdout.strip() or None
                        break
                except Exception:
                    pass
            else:
                self._backing_cache[device] = None
        return self._backing_cache[device]

    def clear_cache(self, device: str | None = None):
        if device is None:
            self._backing_cache.clear()
        else:
            self._backing_cache.pop(device, None)

    # ── low-level I/O ────────────────────────────────────────────

    def read(self, device: str, offset: int, size: int) -> bytes:
        backing = self._backing_file(device)
        if backing and os.access(backing, os.R_OK):
            fd = os.open(backing, os.O_RDONLY)
            try:
                return os.pread(fd, size, offset)
            finally:
                os.close(fd)
        cmd = ['sudo', 'dd', f'if={device}', 'bs=1', f'skip={offset}',
               f'count={size}', 'status=none']
        r = subprocess.run(cmd, capture_output=True)
        return r.stdout

    def write(self, device: str, offset: int, data: bytes):
        backing = self._backing_file(device)
        if backing and os.access(backing, os.W_OK):
            fd = os.open(backing, os.O_WRONLY)
            try:
                n = os.pwrite(fd, data, offset)
                assert n == len(data)
                os.fsync(fd)
            finally:
                os.close(fd)
            return
        with tempfile.NamedTemporaryFile() as tf:
            tf.write(data)
            tf.flush()
            cmd = ['sudo', 'dd', f'if={tf.name}', f'of={device}',
                   'bs=1', f'seek={offset}', f'count={len(data)}',
                   'status=none', 'conv=fsync']
            subprocess.run(cmd, check=True, capture_output=True)

    # ── boot sector ──────────────────────────────────────────────

    def parse_boot(self, device: str):
        data = self.read(device, 0, 512)
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
