"""Shared test utilities."""

import gzip
import shutil
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
    """Decompress sda1_sparse.img.gz to a temp file.

    Returns (img_path, temp_dir). Caller must clean up temp_dir.
    """
    temp_dir = Path(tempfile.mkdtemp(prefix='gopro_test_'))
    img_path = temp_dir / 'sda1_sparse.img'
    with gzip.open(gz_path, 'rb') as src, open(img_path, 'wb') as dst:
        shutil.copyfileobj(src, dst)
    return img_path, temp_dir
