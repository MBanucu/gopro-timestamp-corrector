"""
Pure calculator — no file I/O.

All datetimes are naive UTC internally. Timezone conversion happens
only at the display layer (gui_file_table.py).
"""

from datetime import datetime, timedelta


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
