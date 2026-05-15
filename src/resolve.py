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


def weighted_median_delta(
    deltas: list[timedelta],
    weights: list[float],
) -> timedelta | None:
    """Weighted median of timedelta list — robust against outliers.

    Sorts by delta, then walks accumulating weight until the cumulative
    sum reaches half of the total weight.  Returns ``None`` if the input
    is empty or total weight is zero.
    """
    if not deltas:
        return None
    pairs = sorted(zip(deltas, weights), key=lambda x: x[0])
    total = sum(weights)
    if total <= 0:
        return None
    cumulative = 0.0
    half = total / 2.0
    for delta, w in pairs:
        cumulative += w
        if cumulative >= half:
            return delta
    return pairs[-1][0]
