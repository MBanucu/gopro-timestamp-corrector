from datetime import datetime
from pathlib import Path


def parse(path):
    text = Path(path).read_text()
    data = {}
    section = None
    last_key = None

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith('## actual local time') or line.startswith('## actual time') or line.startswith('## current time'):
            section = 'actual'
            data[section] = {}
            continue
        elif line.startswith('## GoPro'):
            section = 'gopro'
            data[section] = {}
            continue
        if section is None:
            continue
        if ':' in line:
            key, value = line.split(':', 1)
            key = key.strip()
            value = value.strip()
            if key in ('date', 'time'):
                data[section][key] = value
                last_key = key
            elif key == 'format':
                data[section][f'{last_key}_format'] = value

    actual = data.get('actual', {})
    gopro = data.get('gopro', {})

    if not actual or not gopro:
        raise ValueError("Could not parse translation file")

    actual_dt = datetime.strptime(f"{actual['date']} {actual['time']}", "%Y-%m-%d %H:%M")
    gopro_dt = datetime.strptime(f"{gopro['date']} {gopro['time']}", "%m/%d/%y %H:%M")

    return actual_dt, gopro_dt
