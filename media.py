import os
import re
import subprocess
from datetime import datetime
from pathlib import Path


MEDIA_PATTERNS = ('*.mp4', '*.MP4', '*.lrv', '*.LRV', '*.thm', '*.THM')
QT_BASE = ['exiftool', '-api', 'QuickTimeUTC=1']


def exiftool_available():
    try:
        subprocess.run(['exiftool', '-ver'], capture_output=True)
        return True
    except FileNotFoundError:
        return False


def collect(directory):
    files = []
    for ext in MEDIA_PATTERNS:
        files.extend(Path(directory).glob(ext))
    return sorted(f for f in files if not f.name.startswith('.'))


def _strip_tz(val):
    return re.sub(r'\s*[+-]\d{2}:\d{2}$', '', val).strip()


def _read_tag(filepath, tag, use_qt_utc=False):
    cmd = (QT_BASE if use_qt_utc else ['exiftool']) + ['-b', tag, str(filepath)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        val = _strip_tz(result.stdout.strip())
        return datetime.strptime(val, "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def read_embedded(filepath, use_qt_utc=True):
    ext = Path(filepath).suffix.lower()
    if ext in ('.mp4', '.lrv'):
        ts = _read_tag(filepath, '-QuickTime:CreateDate', use_qt_utc=use_qt_utc)
        return ts if ts else _read_tag(filepath, '-QuickTime:MediaCreateDate', use_qt_utc=use_qt_utc)
    elif ext == '.thm':
        return _read_tag(filepath, '-EXIF:DateTimeOriginal')
    return None


def read_mtime(filepath):
    return datetime.fromtimestamp(os.path.getmtime(filepath))


def write_embedded(filepath, dt):
    ext = Path(filepath).suffix.lower()
    fmt = dt.strftime("%Y:%m:%d %H:%M:%S")

    if ext in ('.mp4', '.lrv'):
        tags = [
            f'-QuickTime:CreateDate={fmt}',
            f'-QuickTime:CreationDate={fmt}',
            f'-QuickTime:ModifyDate={fmt}',
            f'-QuickTime:MediaCreateDate={fmt}',
            f'-QuickTime:MediaModifyDate={fmt}',
            f'-QuickTime:TrackCreateDate={fmt}',
            f'-QuickTime:TrackModifyDate={fmt}',
        ]
        cmd = QT_BASE + ['-overwrite_original'] + tags + [str(filepath)]
    elif ext == '.thm':
        tags = [
            f'-EXIF:DateTimeOriginal={fmt}',
            f'-EXIF:CreateDate={fmt}',
            f'-EXIF:ModifyDate={fmt}',
        ]
        cmd = ['exiftool', '-overwrite_original'] + tags + [str(filepath)]
    else:
        return False

    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0


def write_mtime(filepath, dt):
    ts = dt.timestamp()
    os.utime(filepath, (ts, ts))
