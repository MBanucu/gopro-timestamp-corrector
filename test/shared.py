"""Shared test utilities."""

import gzip
import os
import tempfile
from pathlib import Path

import tkinter as tk
from tkinter import ttk

from tzcombobox import FilteringCombobox


TEST_ZONES = [
    'EET', 'EST', 'Europe/Amsterdam', 'Europe/Berlin',
    'Europe/London', 'Europe/Paris', 'Europe/Rome',
    'Europe/Vienna', 'Europe/Zurich', 'Eire',
]


def make_cb(root):
    cb = FilteringCombobox(root, all_values=TEST_ZONES, width=30)
    cb.pack()
    root.update_idletasks()
    return cb


def decompress_sparse_image(gz_path: Path) -> tuple[Path, Path]:
    """Decompress sda1_sparse.img.gz to a sparse temp file.

    Writes non-zero data blocks directly into a sparse file without
    ever creating a dense 8GB intermediate file on disk.

    Returns (img_path, temp_dir).  Caller must clean up temp_dir.
    """
    temp_dir = Path(tempfile.mkdtemp(prefix='gopro_test_'))
    img_path = temp_dir / 'sda1_sparse.img'
    _write_sparse(gz_path, img_path)
    return img_path, temp_dir


def _write_sparse(gz_path: Path, img_path: Path):
    """Stream gzip content into a sparse file, writing only non-zero blocks."""
    KNOWN_SIZE = 8531738624  # apparent (uncompressed) size of sda1_sparse.img
    CHUNK = 1024 * 1024      # 1 MiB

    # Create a sparse file of the right apparent size (0 disk usage for holes).
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
