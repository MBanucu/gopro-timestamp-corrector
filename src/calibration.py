from datetime import datetime, timezone
from pathlib import Path

from options import CAL_DATE_FORMAT, CAL_TIME_FORMAT


VERSION = 1
DEFAULT = {
    "version": VERSION,
    "description": "GoPro time calibration reference",
    "actual": {
        "date": "",
        "date_format": CAL_DATE_FORMAT,
        "time": "",
        "time_format": CAL_TIME_FORMAT,
        "timezone": "",
        "fold": 0,
    },
    "gopro": {
        "date": "",
        "date_format": CAL_DATE_FORMAT,
        "time": "",
        "time_format": CAL_TIME_FORMAT,
        "timezone": "",
        "fold": 0,
    },
}


def default():
    import copy
    return copy.deepcopy(DEFAULT)


def load_json(path):
    import json
    with open(path) as f:
        data = json.load(f)
    validate(data)
    return data


def save_json(path, data):
    import json
    validate(data)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
        f.write('\n')


def validate(data):
    for side in ('actual', 'gopro'):
        s = data.get(side, {})
        for key in ('date', 'date_format', 'time', 'time_format'):
            if key not in s:
                raise ValueError(f"Missing '{side}.{key}'")


def try_parse(data):
    try:
        actual_dt, gopro_dt = parse_data(data)
        return True, actual_dt, gopro_dt
    except Exception as e:
        return False, None, str(e)


def parse_data(data):
    a = data['actual']
    g = data['gopro']
    actual_dt = _parse_with_tz(a['date'], a['time'], a.get('timezone', ''), a.get('fold', 0))
    gopro_dt = _parse_with_tz(g['date'], g['time'], g.get('timezone', ''), g.get('fold', 0))
    return actual_dt, gopro_dt


def _parse_with_tz(date_str, time_str, tz_id, fold=0):
    fmt = '%Y-%m-%d'
    try:
        dt = datetime.strptime(f"{date_str} {time_str}", f"{fmt} %H:%M:%S.%f")
    except ValueError:
        try:
            dt = datetime.strptime(f"{date_str} {time_str}", f"{fmt} %H:%M:%S")
        except ValueError:
            try:
                dt = datetime.strptime(f"{date_str} {time_str}", f"{fmt} %H:%M")
            except ValueError:
                dt = datetime.strptime(f"{date_str} {time_str}", "%m/%d/%y %H:%M")
    if tz_id:
        try:
            import zoneinfo
            tz = zoneinfo.ZoneInfo(tz_id)
            dt = dt.replace(tzinfo=tz, fold=fold)
        except Exception:
            dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_text(data):
    import datetime as dt_mod
    a = data['actual']
    g = data['gopro']
    g_dt = _parse_with_tz(g['date'], g['time'], '', 0)
    g_date_mdy = g_dt.strftime("%m/%d/%y")

    lines = [
        "# this is the measurement of one specific point in time "
        "to calculate the time difference between actual time and GoPro time",
        "",
    ]
    label_a = "## actual local time"
    if a.get('timezone'):
        label_a += f" ({a['timezone']})"
    lines.append(label_a)
    lines.append("")
    lines.append(f"date: {a['date']}")
    lines.append("format: year-month-day")
    lines.append("")
    lines.append(f"time: {a['time']}")
    lines.append("format: hour:minute")
    lines.append("")

    label_g = "## GoPro local time"
    if g.get('timezone'):
        label_g += f" ({g['timezone']})"
    lines.append(label_g)
    lines.append("")
    lines.append(f"time: {g['time']}")
    lines.append("format: hour:minute")
    lines.append("")
    lines.append(f"date: {g_date_mdy}")
    lines.append("format: month/day/year")
    lines.append("")
    return '\n'.join(lines)


def from_text(path):
    import translate
    actual_dt, gopro_dt = translate.parse(path)
    data = default()
    data['actual']['date'] = actual_dt.strftime("%Y-%m-%d")
    data['actual']['time'] = actual_dt.strftime("%H:%M")
    data['gopro']['date'] = gopro_dt.strftime("%Y-%m-%d")
    data['gopro']['time'] = gopro_dt.strftime("%H:%M")
    return data


def load_auto(path):
    p = Path(path)
    if p.suffix.lower() == '.json':
        return load_json(path), 'json'
    return from_text(path), 'text'
