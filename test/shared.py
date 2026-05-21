"""Shared test utilities."""

import gzip
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HAS_TK = False
try:
    import tkinter as tk
    _r = tk.Tk()
    _r.destroy()
    HAS_TK = True
except Exception:
    pass


TEST_ZONES = [
    'EET', 'EST', 'Europe/Amsterdam', 'Europe/Berlin',
    'Europe/London', 'Europe/Paris', 'Europe/Rome',
    'Europe/Vienna', 'Europe/Zurich', 'Eire',
]


def make_cb(root):
    from gui.tzcombobox import FilteringCombobox
    cb = FilteringCombobox(root, all_values=TEST_ZONES, width=30)
    cb.pack()
    root.update_idletasks()
    return cb


def decompress_sparse_image(gz_path: Path, dest_path: Path) -> Path:
    """Decompress *gz_path* → *dest_path* if not already present.

    Writes non-zero data blocks directly into a sparse file without
    ever creating a dense 8 GB intermediate file on disk.

    Returns *dest_path* (already exists or freshly decompressed).
    """
    if dest_path.exists():
        return dest_path
    write_sparse(gz_path, dest_path)
    return dest_path


def write_sparse(gz_path: Path, img_path: Path):
    """Stream gzip content into a sparse file, writing only non-zero blocks."""
    KNOWN_SIZE = 8531738624  # apparent (uncompressed) size of sdcard.img
    CHUNK = 1024 * 1024      # 1 MiB

    fd = os.open(img_path, os.O_CREAT | os.O_WRONLY)
    os.ftruncate(fd, KNOWN_SIZE)
    os.close(fd)

    zero = b'\x00' * CHUNK
    offset = 0

    with gzip.open(gz_path, 'rb') as src, open(img_path, 'rb+') as dst:
        while True:
            chunk = src.read(CHUNK)
            if not chunk:
                break
            if chunk != zero[:len(chunk)]:
                os.lseek(dst.fileno(), offset, os.SEEK_SET)
                dst.write(chunk)
            offset += len(chunk)


def _loop_via_udisksctl(img_path):
    """Try udisksctl loop-setup + mount. Returns (loop_dev, mount_point) or None."""
    try:
        r = subprocess.run(
            ['udisksctl', 'loop-setup', '-f', str(img_path),
             '--no-user-interaction'],
            capture_output=True, text=True)
        if r.returncode != 0:
            return None
        m = re.search(r'as (/dev/loop\d+)', r.stdout)
        if not m:
            return None
        loop_dev = m.group(1)

        r = subprocess.run(
            ['udisksctl', 'mount', '-b', loop_dev, '--no-user-interaction'],
            capture_output=True, text=True)
        if r.returncode != 0:
            subprocess.run(['sudo', 'losetup', '-d', loop_dev],
                           capture_output=True)
            return None
        m = re.search(r'at ([^ \n]+)', r.stdout)
        if not m:
            subprocess.run(['sudo', 'losetup', '-d', loop_dev],
                           capture_output=True)
            return None
        return (loop_dev, m.group(1).rstrip('.'))
    except FileNotFoundError:
        return None


def _loop_via_sudo(img_path):
    """Fallback: sudo losetup + sudo mount. Returns (loop_dev, mount_point) or None."""
    try:
        r = subprocess.run(
            ['sudo', 'losetup', '-f', '--show', str(img_path)],
            capture_output=True, text=True)
        if r.returncode != 0:
            return None
        loop_dev = r.stdout.strip()

        mount_point = tempfile.mkdtemp(prefix='gopro_mnt_')

        subprocess.run(['sudo', 'chmod', '666', loop_dev],
                       capture_output=True)

        uid = os.getuid()
        gid = os.getgid()
        for fs_type in ('exfat', 'fuse.exfat', 'auto'):
            r = subprocess.run(
                ['sudo', 'mount', '-t', fs_type,
                 '-o', f'uid={uid},gid={gid}',
                 loop_dev, mount_point],
                capture_output=True, text=True)
            if r.returncode == 0:
                break
        if r.returncode != 0:
            r = subprocess.run(
                ['sudo', 'mount.exfat-fuse', loop_dev, mount_point],
                capture_output=True, text=True)
        if r.returncode != 0:
            subprocess.run(['sudo', 'losetup', '-d', loop_dev],
                           capture_output=True)
            os.rmdir(mount_point)
            return None
        return (loop_dev, mount_point)
    except FileNotFoundError:
        return None


def setup_loop_device(img_path):
    """Set up loop device and mount exFAT image.

    Returns (loop_dev, mount_point) on success.
    Raises unittest.SkipTest on failure.
    """
    result = _loop_via_udisksctl(img_path)
    if result is not None:
        return result
    result = _loop_via_sudo(img_path)
    if result is not None:
        return result
    raise unittest.SkipTest("Could not set up loop device (udisksctl+sudo failed)")


def teardown_loop_device(loop_dev, mount_point=None):
    """Unmount and detach loop device."""
    if loop_dev:
        r = subprocess.run(['sudo', 'umount', loop_dev], capture_output=True)
        subprocess.run(['sudo', 'losetup', '-d', loop_dev], capture_output=True)
