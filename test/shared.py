"""Shared test utilities."""

import gzip
import os
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
