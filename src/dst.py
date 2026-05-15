from datetime import datetime, timedelta, time
try:
    import zoneinfo
except ImportError:
    zoneinfo = None


def get_transition_hour(tz_id, date):
    """Return the hour (0-23) of the DST transition on this date, or None."""
    tz = zoneinfo.ZoneInfo(tz_id)
    offset_before = None
    for h in range(24):
        dt = datetime.combine(date, time(h, 0))
        dt_tz = dt.replace(tzinfo=tz)
        offset = tz.utcoffset(dt)
        if offset_before is not None and offset != offset_before:
            return h
        offset_before = offset
    return None


def check(tz_id, dt):
    """Check if a given datetime is in a DST-ambiguous window.
    
    Returns a dict with keys:
      - ambiguous: bool
      - transition_hour: int or None
      - transition_type: 'spring_forward' | 'fall_back' | None
      - before_offset, after_offset: str
      - abbr_before, abbr_after: str
      - fold: 0 (first occurrence) or 1 (second occurrence)
      - message: str
    """
    result = {
        'ambiguous': False,
        'transition_hour': None,
        'transition_type': None,
        'before_offset': '',
        'after_offset': '',
        'abbr_before': '',
        'abbr_after': '',
        'fold': 0,
        'message': '',
    }
    if not zoneinfo or not tz_id or not dt:
        return result

    try:
        tz = zoneinfo.ZoneInfo(tz_id)
    except Exception:
        return result

    date = dt.date()
    trans_hour = get_transition_hour(tz_id, date)
    if trans_hour is None:
        return result

    t_before = datetime.combine(date, time(trans_hour - 1, 0)).replace(tzinfo=tz)
    t_after = datetime.combine(date, time(trans_hour, 0)).replace(tzinfo=tz)
    off_before = tz.utcoffset(t_before)
    off_after = tz.utcoffset(t_after)

    if off_before is None or off_after is None:
        return result

    result['transition_hour'] = trans_hour
    result['before_offset'] = str(off_before)
    result['after_offset'] = str(off_after)
    result['abbr_before'] = t_before.tzname() or ''
    result['abbr_after'] = t_after.tzname() or ''

    spring = off_after > off_before
    result['transition_type'] = 'spring_forward' if spring else 'fall_back'

    # The ambiguous/missing window is [trans_hour - 1, trans_hour)
    window_start = trans_hour - 1
    dt_user = dt.replace(tzinfo=tz)
    dt_utc = dt_user.astimezone(zoneinfo.ZoneInfo('UTC'))

    if dt.hour < window_start:
        result['fold'] = 0
    elif dt.hour >= trans_hour:
        result['fold'] = 1 if not spring else 0
    else:
        # Inside the transition window
        if spring:
            # This hour is skipped entirely (spring-forward)
            result['fold'] = -1
            result['ambiguous'] = True
            result['message'] = (
                f"⚠ Spring-forward in {tz_id}: {result['abbr_before']} → {result['abbr_after']}, "
                f"transition at {trans_hour:02d}:00. "
                f"The hour {window_start:02d}:00–{trans_hour:02d}:00 does not exist! "
                f"(Your time {dt.hour:02d}:{dt.minute:02d} is in this gap.)"
            )
        else:
            # Fall-back: this hour occurs twice
            result['fold'] = 0  # first occurrence (CEST)
            result['ambiguous'] = True
            result['message'] = (
                f"⚠ Fall-back in {tz_id}: {result['abbr_before']} → {result['abbr_after']}, "
                f"transition at {trans_hour:02d}:00. "
                f"The hour {window_start:02d}:00–{trans_hour:02d}:00 occurs twice. "
                f"Currently: {result['abbr_before']} (fold=0 / first occurrence). "
                f"Use the fold selector below to switch to {result['abbr_after']} (fold=1)."
            )

    return result

    try:
        tz = zoneinfo.ZoneInfo(tz_id)
    except Exception:
        return result

    date = dt.date()
    trans_hour = get_transition_hour(tz_id, date)
    if trans_hour is None:
        return result

    t_before = datetime.combine(date, time(trans_hour - 1, 0)).replace(tzinfo=tz)
    t_after = datetime.combine(date, time(trans_hour, 0)).replace(tzinfo=tz)
    off_before = tz.utcoffset(t_before)
    off_after = tz.utcoffset(t_after)

    if off_before is None or off_after is None:
        return result

    result['transition_hour'] = trans_hour
    result['before_offset'] = str(off_before)
    result['after_offset'] = str(off_after)
    result['abbr_before'] = t_before.tzname() or ''
    result['abbr_after'] = t_after.tzname() or ''

    spring = off_after > off_before
    result['transition_type'] = 'spring_forward' if spring else 'fall_back'

    dt_user = dt.replace(tzinfo=tz)
    dt_user_utc = dt_user.astimezone(zoneinfo.ZoneInfo('UTC'))
    # fold=0: before transition (first occurrence), fold=1: after (second)
    if dt.hour < trans_hour:
        result['fold'] = 0
    elif dt.hour > trans_hour:
        result['fold'] = 1
    else:
        result['fold'] = -1  # exactly at transition, ambiguous
        result['ambiguous'] = True
        result['message'] = (
            f"Ambiguous time: {dt.hour:02d}:{dt.minute:02d} exists twice "
            f"in {tz_id} on {date}. "
            f"First at {result['before_offset']} ({result['abbr_before']}), "
            f"second at {result['after_offset']} ({result['abbr_after']})."
        )
        return result

    # Check if the user's time could be ambiguous
    fall_back_window = [trans_hour, trans_hour + 1] if not spring else [trans_hour - 1, trans_hour]
    if dt.hour in fall_back_window:
        result['ambiguous'] = True
        kind = 'Fall-back' if not spring else 'Spring-forward'
        result['message'] = (
            f"{kind} in {tz_id}: {result['abbr_before']} → {result['abbr_after']}, "
            f"transition at {trans_hour:02d}:00. "
            f"Time: {result['abbr_before']} (fold={result['fold']})."
        )

    return result


def find_ambiguous_periods(tz_id, year):
    """Return all dates in a year with DST transitions for a timezone."""
    if not zoneinfo:
        return []
    result = []
    try:
        tz = zoneinfo.ZoneInfo(tz_id)
    except Exception:
        return result
    for day in range(365):
        d = datetime(year, 1, 1) + timedelta(days=day)
        trans_hour = get_transition_hour(tz_id, d)
        if trans_hour is not None:
            result.append((d, trans_hour))
    return result
