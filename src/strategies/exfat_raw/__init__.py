"""Raw exFAT block-read/write operations.

Layers (one file per layer):
  ``_pure``       — CRC, time encoding/decoding (stateless)
  ``_io``         — ``ExfatRawIO`` (backing-file cache + low-level read/write + boot parse)
  ``_fs``         — ``ExfatRawFilesystem`` (FAT, clusters, directory traversal)
  ``_ops``        — ``ExfatRawOps`` (high-level read/write of btime/mtime)
  ``_strategy``   — ``ExfatRawStrategy`` / ``ExfatRawReadStrategy`` (BtimeStrategy adapters)

Singletons
==========
``exfat_io`` — default ``ExfatRawIO`` instance
``exfat_ops`` — default ``ExfatRawOps`` instance composed from ``exfat_io`` + ``ExfatRawFilesystem``

Tests should create their own ``ExfatRawIO()`` / ``ExfatRawOps()`` instances for cache isolation.
"""

from strategies.exfat_raw._io import ExfatRawIO
from strategies.exfat_raw._fs import ExfatRawFilesystem
from strategies.exfat_raw._ops import ExfatRawOps
from strategies.exfat_raw._strategy import ExfatRawStrategy, ExfatRawReadStrategy

exfat_io: ExfatRawIO = ExfatRawIO()
exfat_fs: ExfatRawFilesystem = ExfatRawFilesystem(exfat_io)
exfat_ops: ExfatRawOps = ExfatRawOps(exfat_io, exfat_fs)

__all__ = [
    'ExfatRawIO',
    'ExfatRawFilesystem',
    'ExfatRawOps',
    'ExfatRawStrategy',
    'ExfatRawReadStrategy',
    'exfat_io',
    'exfat_fs',
    'exfat_ops',
]
