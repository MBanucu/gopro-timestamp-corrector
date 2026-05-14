"""
Shared time-resolution functions used by preview and correction modules.

All datetimes are naive UTC internally. Timezone conversion happens
only at the display layer (gui_file_table.py).
"""

from datetime import datetime, timedelta
from pathlib import Path

import media


def target_time(current: datetime | None, delta: timedelta | None) -> datetime | None:
    """Apply delta to get corrected target time."""
    if current is None or delta is None:
        return None
    return current + delta


def gps_delta(gps_utc: datetime | None, embedded_utc: datetime | None) -> timedelta | None:
    """Compute correction delta from GPS UTC and embedded (UTC)."""
    if gps_utc is None or embedded_utc is None:
        return None
    return gps_utc - embedded_utc


def read_current(filepath: Path, already_read: dict | None = None) -> tuple[datetime | None, str]:
    """
    Read current timestamp from a file, with partner fallback for THM.

    For THM files without embedded data, falls back to a paired MP4/LRV
    that was already read (passed via *already_read* dict).
    Final fallback is filesystem mtime.

    Returns (datetime_or_None, source_label).
    """
    dt = media.read_embedded(filepath, use_qt_utc=False)
    if dt is not None:
        return dt, 'embedded'

    if filepath.suffix.lower() == '.thm' and already_read is not None:
        for ext in ('.MP4', '.mp4', '.LRV', '.lrv'):
            partner = filepath.with_suffix(ext)
            if partner.exists() and partner in already_read:
                return already_read[partner][0], f'matched {partner.name}'

    dt = media.read_mtime(filepath)
    if dt is not None:
        return dt, 'mtime'

    return None, 'none'
