"""Managed exiftool session.

Connects to the shared ``ExifToolServer`` by default
(``connect='auto'``).  Only the server itself uses
``ExifToolSession(connect=None)`` for direct PyExifTool access.
"""

import json as _json
import os
import re
import socket
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from exiftool import ExifToolHelper
from exiftool.client import ExifToolClient
from exiftool.server import find_server, spawn_server
from exiftool.exceptions import ExifToolConnectionError

from options import EXIFTOOL_SERVER_PORT_FILE


_PORT_FILE = os.path.join(tempfile.gettempdir(), EXIFTOOL_SERVER_PORT_FILE)


def _ensure_server(port_file: str) -> int:
    """Find or auto-spawn the ExifTool server. Returns its port."""
    port = find_server(port_file=port_file)
    if port is not None:
        return port

    import fcntl
    lock_file = port_file + '.client.lock'
    with open(lock_file, 'w') as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        port = find_server(port_file=port_file)
        if port is not None:
            return port
        return spawn_server(port_file=port_file, singleton=True)


def _parse_dt(val: str) -> datetime | None:
    """Parse an exiftool date string into a UTC-aware datetime.

    exiftool outputs dates in local time with a timezone offset
    (e.g. ``2026:05:14 14:52:00+09:00``).  This function extracts
    the offset and converts to UTC, so the returned datetime always
    carries ``tzinfo=timezone.utc``.
    """
    val = str(val).strip()
    if not val:
        return None
    try:
        offset = timedelta()
        m = re.search(r'\s*([+-])(\d{2}):(\d{2})\s*$', val)
        if m:
            sign = 1 if m.group(1) == '+' else -1
            offset = timedelta(hours=int(m.group(2)),
                               minutes=int(m.group(3))) * sign
            val = val[:m.start()]
        elif val.endswith('Z'):
            val = val[:-1].strip()
        val = val.strip()
        if '.' in val:
            main, frac = val.split('.')
            frac = (frac + '000000')[:6]
            dt = datetime.strptime(f'{main}.{frac}',
                                   '%Y:%m:%d %H:%M:%S.%f')
        else:
            dt = datetime.strptime(val, '%Y:%m:%d %H:%M:%S')
        return dt.replace(tzinfo=timezone.utc) - offset
    except (ValueError, IndexError):
        return None


class ExifToolSession:
    """Persistent exiftool session.

    Usage::

        with ExifToolSession() as session:
            embedded, gps = session.read_tags_batch(files)[path]

    When *connect* is ``"auto"``, delegates to a shared ``ExifToolServer``
    running as a background process (auto-spawned if not running).

    When *connect* is ``None``, starts a private exiftool subprocess
    directly (used internally by the server process).
    """

    def __init__(self, helper: ExifToolHelper | None = None,
                 *, connect: str | None = 'auto',
                 port_file: str | None = None):
        if helper is not None:
            self._client = None
            self._et = helper
        elif connect == 'auto':
            pf = port_file or _PORT_FILE
            port = _ensure_server(pf)
            self._client = ExifToolClient(host='127.0.0.1', port=port)
            self._et = None
        else:
            self._client = None
            self._et = ExifToolHelper()

    @property
    def _executor(self):
        return self._client if self._client is not None else self._et

    def __enter__(self):
        if self._et is not None:
            self._et.__enter__()
        return self

    def __exit__(self, *args):
        if self._et is not None:
            self._et.__exit__(*args)

    # ── Availability ──────────────────────────────────────────────────────

    def available(self) -> bool:
        try:
            self._executor.execute('-ver')
            return True
        except Exception:
            return False

    # ── Single-file reads ─────────────────────────────────────────────────

    def read_gps_time(self, filepath: str | Path) -> datetime | None:
        """Read the first ``GPSDateTime`` from *filepath*."""
        result = self._executor.execute(
            '-ee', '-s3', '-GPSDateTime', str(filepath))
        if not result.strip():
            return None
        line = result.strip().splitlines()[0].strip()
        return _parse_dt(line)

    def read_embedded(self, filepath: str | Path,
                      use_qt_utc: bool = True) -> datetime | None:
        """Read embedded time from a single file."""
        ext = Path(filepath).suffix.lower()
        tags_label = (['-QuickTime:CreateDate', '-QuickTime:MediaCreateDate']
                      if ext in ('.mp4', '.lrv')
                      else ['-EXIF:DateTimeOriginal'])
        args = ['-json']
        if use_qt_utc:
            args += ['-api', 'QuickTimeUTC=1']
        args += tags_label + [str(filepath)]
        raw = self._executor.execute(*args)
        return self._parse_single_embedded(raw, ext)

    def _parse_single_embedded(self, raw: str, ext: str) -> datetime | None:
        if not raw.strip():
            return None
        try:
            rec = _json.loads(raw)
        except Exception:
            return None
        if not rec:
            return None
        rec = rec[0] if isinstance(rec, list) else rec
        if ext in ('.mp4', '.lrv'):
            for tag in ('QuickTime:CreateDate', 'QuickTime:MediaCreateDate',
                        'CreateDate', 'MediaCreateDate'):
                raw_val = rec.get(tag)
                if raw_val:
                    dt = _parse_dt(str(raw_val))
                    if dt:
                        return dt
            return None
        raw_val = (rec.get('EXIF:DateTimeOriginal')
                   or rec.get('DateTimeOriginal'))
        return _parse_dt(str(raw_val)) if raw_val else None

    # ── Batch reads ────────────────────────────────────────────────────────

    def read_tags_batch(
        self, filepaths: list[Path]
    ) -> dict[Path, tuple[datetime | None, datetime | None]]:
        """Read embedded + GPS time for all *filepaths* in one call."""
        if not filepaths:
            return {}

        raw = self._executor.execute(
            '-json', '-ee',
            '-QuickTime:CreateDate', '-QuickTime:MediaCreateDate',
            '-EXIF:DateTimeOriginal', '-GPSDateTime',
            *[str(p) for p in filepaths],
        )
        if not raw.strip():
            return {}
        try:
            records = _json.loads(raw)
        except Exception:
            return {}

        out: dict[Path, tuple[datetime | None, datetime | None]] = {}
        for rec in records:
            src = rec.get('SourceFile')
            if not src:
                continue
            path = Path(src)

            embedded: datetime | None = None
            for tag in ('QuickTime:CreateDate', 'QuickTime:MediaCreateDate',
                        'EXIF:DateTimeOriginal',
                        'CreateDate', 'MediaCreateDate',
                        'DateTimeOriginal'):
                raw_val = rec.get(tag)
                if raw_val:
                    embedded = _parse_dt(str(raw_val))
                    if embedded:
                        break

            gps_time: datetime | None = None
            gps_raw_val = (rec.get('GoPro:GPSDateTime')
                           or rec.get('GPSDateTime'))
            if gps_raw_val:
                gps_time = _parse_dt(str(gps_raw_val))

            out[path] = (embedded, gps_time)

        return out

    def read_gps_accuracy_batch(
        self, filepaths: list[Path]
    ) -> dict[Path, float | None]:
        """Read ``GPSHPositioningError`` for all *filepaths*."""
        if not filepaths:
            return {}

        raw = self._executor.execute(
            '-json', '-ee', '-GPSHPositioningError',
            *[str(p) for p in filepaths],
        )
        if not raw.strip():
            return {}
        try:
            records = _json.loads(raw)
        except Exception:
            return {}

        out: dict[Path, float | None] = {}
        for rec in records:
            src = rec.get('SourceFile')
            if not src:
                continue
            raw_val = (rec.get('GoPro:GPSHPositioningError')
                       or rec.get('GPSHPositioningError')
                       or rec.get('Composite:GPSHPositioningError'))
            if raw_val is not None:
                try:
                    out[Path(src)] = float(raw_val)
                except (ValueError, TypeError):
                    out[Path(src)] = None
            else:
                out[Path(src)] = None

        return out

    # ── Writes ─────────────────────────────────────────────────────────────

    def write_embedded(self, path: Path, dt: datetime) -> bool:
        """Write embedded metadata to a single file."""
        ext = path.suffix.lower()
        fmt = dt.strftime('%Y:%m:%d %H:%M:%S')
        fmt_creation = dt.strftime('%Y:%m:%d %H:%M:%S+00:00')

        if ext in ('.mp4', '.lrv'):
            tags = [
                f'-QuickTime:CreateDate={fmt}',
                f'-QuickTime:CreationDate={fmt_creation}',
                f'-QuickTime:ModifyDate={fmt}',
                f'-QuickTime:MediaCreateDate={fmt}',
                f'-QuickTime:MediaModifyDate={fmt}',
                f'-QuickTime:TrackCreateDate={fmt}',
                f'-QuickTime:TrackModifyDate={fmt}',
            ]
        elif ext == '.thm':
            tags = [
                f'-EXIF:DateTimeOriginal={fmt}',
                f'-EXIF:CreateDate={fmt}',
                f'-EXIF:ModifyDate={fmt}',
            ]
        else:
            tags = [
                f'-QuickTime:CreateDate={fmt}',
                f'-QuickTime:CreationDate={fmt_creation}',
                f'-QuickTime:ModifyDate={fmt}',
                f'-QuickTime:MediaCreateDate={fmt}',
                f'-QuickTime:MediaModifyDate={fmt}',
                f'-QuickTime:TrackCreateDate={fmt}',
                f'-QuickTime:TrackModifyDate={fmt}',
            ]

        try:
            self._executor.execute('-overwrite_original', *tags, str(path))
            return True
        except Exception:
            return False

    def write_embedded_batch(
        self, pairs: list[tuple[Path, datetime]]
    ) -> bool:
        """Write embedded times for all *pairs*."""
        ok = True
        for path, dt in pairs:
            if not self.write_embedded(path, dt):
                ok = False
        return ok

    # ── History dump ───────────────────────────────────────────────────────

    def dump_full_json(self, filepaths: list[Path]) -> str | None:
        """Full exiftool JSON array for a list of files (history log)."""
        if not filepaths:
            return None
        raw = self._executor.execute(
            '-json', '-G', '-a', '--short',
            *[str(p) for p in filepaths])
        if raw.strip():
            return raw
        return None

    def dump_tags_json(self, filepaths: list[Path],
                       tags: list[str]) -> str | None:
        """Read specific tags in JSON format (raw output string)."""
        if not filepaths:
            return None
        args = ['-json'] + tags + [str(p) for p in filepaths]
        raw = self._executor.execute(*args)
        if raw.strip():
            return raw
        return None
