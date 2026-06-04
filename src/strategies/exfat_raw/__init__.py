"""Re-exports from the external ``exfat-raw`` package plus project-specific adapters.

The actual raw-block I/O implementation lives in the standalone
``exfat-raw`` package (https://github.com/MBanucu/exfat-raw).
This module re-exports its classes and adds the project-specific
``ExfatRawStrategy`` / ``ExfatRawReadStrategy`` adapters.
"""

from exfat_raw import ExfatRawIO, ExfatRawFilesystem, ExfatRawOps

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
