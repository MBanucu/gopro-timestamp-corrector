"""Shared test utilities."""

import gzip
import os
import unittest
from pathlib import Path

from loop_device import (
    LoopDeviceError,
    setup_loop_device as _raw_setup,
    teardown_loop_device as _raw_teardown,
)

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


def setup_loop_device(img_path):
    """Set up loop device and mount exFAT image.

    Returns (loop_dev, mount_point) on success.
    Raises unittest.SkipTest on failure.
    """
    try:
        return _raw_setup(str(img_path))
    except LoopDeviceError as e:
        raise unittest.SkipTest(str(e))


def teardown_loop_device(loop_dev, mount_point=None):
    """Unmount and detach loop device."""
    _raw_teardown(loop_dev, mount_point)
