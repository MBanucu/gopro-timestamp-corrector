"""Shared test utilities."""

import gzip
import os
import shutil
import subprocess
import tempfile
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

    Uses ``fcntl.flock`` on a sidecar lockfile so that concurrent
    callers (e.g. parallel subprocesses) do not race on the cache.

    Returns *dest_path* (already exists or freshly decompressed).
    """
    import fcntl
    lock_path = dest_path.with_suffix('.img.lock')
    with open(lock_path, 'w') as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        if not dest_path.exists():
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


_SPARSE = os.environ.get('GOPRO_SPARSE_COPY', '1') != '0'


def prepare_sparse_image(gz_path: Path, prefix: str = 'gopro_') -> tuple[Path, Path]:
    """Decompress *gz_path* to a cached location (if needed), then copy
    to an isolated temp working directory.

    When ``GOPRO_SPARSE_COPY=0`` the copy is fully allocated
    (``--sparse=never``) to avoid loop-device REQ_NOWAIT + qcow2 EIO.

    Otherwise the copy uses ``--sparse=always``.

    Returns ``(temp_dir, image_copy_path)``.
    """
    cached = gz_path.with_suffix('')  # e.g. sdcard.img.gz → sdcard.img
    decompress_sparse_image(gz_path, cached)

    work_dir = Path(tempfile.mkdtemp(prefix=prefix))
    img_copy = work_dir / cached.name
    flag = '--sparse=never' if not _SPARSE else '--sparse=always'
    subprocess.run(
        ['cp', flag, str(cached), str(img_copy)],
        check=True, capture_output=True,
    )
    return work_dir, img_copy


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
