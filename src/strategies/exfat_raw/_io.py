"""Low-level raw block I/O for exFAT filesystems.

I/O goes through the **backing file** (when available) via fast
``os.pread``/``os.pwrite`` — single syscall, no subprocess overhead.

For physical devices (no backing file), falls back to ``sudo dd``
through the raw device path.
"""

import os
import struct
import subprocess
import tempfile


class ExfatRawIO:
    """Raw block I/O — prefers backing file; falls back to ``sudo dd``."""

    # ── backing-file resolution (no cache) ────────────────────────

    def _backing_file(self, device: str) -> str | None:
        """Resolve the backing file for *device* via sysfs.

        Always re-reads from sysfs (no cache) to detect TOCTOU races
        where another process reassigned our loop device.
        """
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
                    return r.stdout.strip() or None
            except Exception:
                pass
        return None

    def clear_cache(self, device: str | None = None):
        pass  # no-op — no backing-file cache to clear

    # ── low-level I/O ────────────────────────────────────────────

    def read(self, device: str, offset: int, size: int) -> bytes:
        backing = self._backing_file(device)
        if backing and os.access(backing, os.R_OK):
            fd = os.open(backing, os.O_RDONLY)
            try:
                return os.pread(fd, size, offset)
            finally:
                os.close(fd)
        # Fallback for physical devices
        cmd = ['sudo', 'dd', f'if={device}', 'bs=1',
               f'skip={offset}', f'count={size}', 'status=none']
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
        # Fallback for physical devices
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
