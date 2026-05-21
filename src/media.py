import os
from datetime import datetime, timezone
from pathlib import Path


MEDIA_PATTERNS = ('*.mp4', '*.MP4', '*.lrv', '*.LRV', '*.thm', '*.THM')


def collect(directory):
    files = []
    for ext in MEDIA_PATTERNS:
        files.extend(Path(directory).glob(ext))
    return sorted(f for f in files if not f.name.startswith('.'))


def read_mtime(filepath):
    ts = os.path.getmtime(filepath)
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def write_mtime(filepath, dt):
    ts = dt.replace(tzinfo=timezone.utc).timestamp()
    os.utime(filepath, (ts, ts))
