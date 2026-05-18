import os
import re
import subprocess
from datetime import datetime, timezone
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
    return re.sub(r'(\s*[+-]\d{2}:\d{2}|Z)$', '', val).strip()


def _read_tag(filepath, tag, use_qt_utc=False):
    cmd = (QT_BASE if use_qt_utc else ['exiftool']) + ['-b', tag, str(filepath)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        val = _strip_tz(result.stdout.strip())
        return datetime.strptime(val, "%Y:%m:%d %H:%M:%S").replace(tzinfo=timezone.utc)
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


def read_gps_time(filepath):
    """Reads the first GPSDateTime from the file using exiftool -ee."""
    cmd = ['exiftool', '-ee', '-s3', '-GPSDateTime', str(filepath)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    # GPSDateTime often looks like "2021:03:11 12:51:00.199Z" or similar
    # We take the first one if there are multiple
    line = result.stdout.strip().splitlines()[0].strip()
    val = _strip_tz(line)
    try:
        if '.' in val:
            main, frac = val.split('.')
            frac = (frac + '000000')[:6]
            val = f"{main}.{frac}"
            return datetime.strptime(val, "%Y:%m:%d %H:%M:%S.%f").replace(tzinfo=timezone.utc)
        return datetime.strptime(val, "%Y:%m:%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (ValueError, IndexError):
        return None


def read_tags_batch(filepaths: list[Path]) -> dict[Path, tuple[datetime | None, datetime | None]]:
    """Read embedded time and GPS time for all *filepaths* in a single exiftool call.

    Returns ``{path: (embedded_time, gps_time)}``.
    """
    if not filepaths:
        return {}

    cmd = (
        ['exiftool', '-json',
         '-QuickTime:CreateDate', '-QuickTime:MediaCreateDate',
         '-EXIF:DateTimeOriginal',
         '-GPSDateTime', '-ee']
        + [str(p) for p in filepaths]
    )
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        return {}

    import json as _json
    records = _json.loads(result.stdout)
    out: dict[Path, tuple[datetime | None, datetime | None]] = {}

    for rec in records:
        src = rec.get('SourceFile')
        if not src:
            continue
        path = Path(src)

        embedded: datetime | None = None
        for tag in ('CreateDate', 'MediaCreateDate', 'DateTimeOriginal'):
            raw = rec.get(tag)
            if raw:
                try:
                    val = _strip_tz(str(raw))
                except Exception:
                    continue
                try:
                    if '.' in val:
                        main, frac = val.split('.')
                        frac = (frac + '000000')[:6]
                        embedded = datetime.strptime(f'{main}.{frac}', '%Y:%m:%d %H:%M:%S.%f')
                        embedded = embedded.replace(tzinfo=timezone.utc)
                    else:
                        embedded = datetime.strptime(val, '%Y:%m:%d %H:%M:%S').replace(tzinfo=timezone.utc)
                    break
                except (ValueError, IndexError):
                    continue

        gps_time: datetime | None = None
        gps_raw = rec.get('GPSDateTime')
        if gps_raw:
            try:
                val = _strip_tz(str(gps_raw))
                if '.' in val:
                    main, frac = val.split('.')
                    frac = (frac + '000000')[:6]
                    gps_time = datetime.strptime(f'{main}.{frac}', '%Y:%m:%d %H:%M:%S.%f')
                    gps_time = gps_time.replace(tzinfo=timezone.utc)
                else:
                    gps_time = datetime.strptime(val, '%Y:%m:%d %H:%M:%S').replace(tzinfo=timezone.utc)
            except (ValueError, IndexError):
                pass

        out[path] = (embedded, gps_time)

    return out


def read_gps_accuracy_batch(filepaths: list[Path]) -> dict[Path, float | None]:
    """Read GPSHPositioningError for all *filepaths* in a single exiftool call.

    Returns ``{path: error_in_meters_or_None}``. A return value of ``99.99``
    typically means no GPS fix. ``None`` means no accuracy data was written.
    """
    if not filepaths:
        return {}

    cmd = (['exiftool', '-json', '-GPSHPositioningError', '-ee']
           + [str(p) for p in filepaths])
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        return {}

    import json as _json
    records = _json.loads(result.stdout)
    out: dict[Path, float | None] = {}

    for rec in records:
        src = rec.get('SourceFile')
        if not src:
            continue
        raw = rec.get('GPSHPositioningError')
        if raw is not None:
            try:
                out[Path(src)] = float(raw)
            except (ValueError, TypeError):
                out[Path(src)] = None
        else:
            out[Path(src)] = None

    return out


def read_mtime(filepath):
    ts = os.path.getmtime(filepath)
    return datetime.fromtimestamp(ts, tz=timezone.utc)


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
        # Target times are always UTC internally, so write without
        # QuickTimeUTC conversion (the value is already UTC).
        cmd = ['exiftool', '-overwrite_original'] + tags + [str(filepath)]
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


_QT_JSON_TAGS = (
    'QuickTime:CreateDate', 'QuickTime:CreationDate',
    'QuickTime:ModifyDate', 'QuickTime:MediaCreateDate',
    'QuickTime:MediaModifyDate', 'QuickTime:TrackCreateDate',
    'QuickTime:TrackModifyDate',
)
_EXIF_JSON_TAGS = (
    'EXIF:DateTimeOriginal', 'EXIF:CreateDate', 'EXIF:ModifyDate',
)


def write_embedded_batch(pairs: list[tuple[Path, datetime]]) -> bool:
    """Write embedded times for all *pairs* in one exiftool call via JSON import.

    Returns ``True`` if all files were written successfully.
    """
    import json as _json
    import tempfile

    qt_records = []
    exif_records = []
    for path, dt in pairs:
        fmt = dt.strftime('%Y:%m:%d %H:%M:%S')
        fmt_creation = dt.strftime('%Y:%m:%d %H:%M:%S+00:00')
        ext = Path(path).suffix.lower()
        if ext in ('.mp4', '.lrv'):
            qt_records.append({'SourceFile': str(path),
                               **{t: fmt for t in _QT_JSON_TAGS},
                               'QuickTime:CreationDate': fmt_creation})
        elif ext == '.thm':
            exif_records.append({'SourceFile': str(path),
                                 **{t: fmt for t in _EXIF_JSON_TAGS}})
        else:
            qt_records.append({'SourceFile': str(path),
                               **{t: fmt for t in _QT_JSON_TAGS},
                               'QuickTime:CreationDate': fmt_creation})

    ok = True
    for records in (qt_records, exif_records):
        if not records:
            continue
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json',
                                          delete=False) as f:
            _json.dump(records, f)
            tmp = f.name
        cmd = ['exiftool', '-overwrite_original', '-json=' + tmp]
        for rec in records:
            cmd.append(rec['SourceFile'])
        result = subprocess.run(cmd, capture_output=True, text=True)
        os.unlink(tmp)
        if result.returncode != 0:
            ok = False
    return ok





def write_mtime(filepath, dt):
    ts = dt.replace(tzinfo=timezone.utc).timestamp()
    os.utime(filepath, (ts, ts))
