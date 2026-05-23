"""Managed exiftool session.

Connects to the shared ``ExifToolServer`` by default
(``connect='auto'``).  Only the server itself uses
``ExifToolSession(connect=None)`` for direct PyExifTool access.

Usage::

    # Connects to shared server (auto-spawned if not running):
    with ExifToolSession() as session:
        embedded, gps = session.read_tags_batch(files)[path]
        ok = session.write_embedded_batch(pairs)

    # Direct mode — only used inside the server process:
    with ExifToolSession(connect=None) as session:
        ...
"""

import json as _json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

from exiftool import ExifToolHelper


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
        # Match optional space then +HH:MM or -HH:MM at end
        m = re.search(r'\s*([+-])(\d{2}):(\d{2})\s*$', val)
        if m:
            sign = 1 if m.group(1) == '+' else -1
            offset = timedelta(hours=int(m.group(2)),
                               minutes=int(m.group(3))) * sign
            val = val[:m.start()]
        elif val.endswith('Z'):
            val = val[:-1].strip()
        # Strip trailing whitespace
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

    Must be used as a context manager::

        with ExifToolSession() as session:
            ...

    When *connect* is ``"auto"``, delegates to a shared ``ExifToolServer``
    running as a background process.  The server is auto-spawned if not
    running.
    """

    def __init__(self, helper: ExifToolHelper | None = None,
                 *, connect: str | None = 'auto'):
        if helper is not None:
            self._client = None
            self._et = helper
        elif connect == 'auto':
            from exiftool_client import ExifToolClient
            self._client = ExifToolClient()
            self._et = None
        else:
            self._client = None
            self._et = ExifToolHelper()

    def __enter__(self):
        if self._et is not None:
            self._et.__enter__()
        return self

    def __exit__(self, *args):
        if self._et is not None:
            self._et.__exit__(*args)
        # Client mode: don't shut down the server — other
        # clients may be using it.

    # ── Availability ──────────────────────────────────────────────────────

    def available(self) -> bool:
        if self._client is not None:
            return self._client.available()
        try:
            self._et.execute('-ver')
            return True
        except Exception:
            return False

    # ── Single-file reads ─────────────────────────────────────────────────

    def read_gps_time(self, filepath: str | Path) -> datetime | None:
        """Read the first ``GPSDateTime`` from *filepath*."""
        if self._client is not None:
            return self._client.read_gps_time(filepath)
        result = self._et.execute('-ee', '-s3', '-GPSDateTime',
                                   str(filepath))
        if not result.strip():
            return None
        line = result.strip().splitlines()[0].strip()
        return _parse_dt(line)

    def read_embedded(self, filepath: str | Path,
                      use_qt_utc: bool = True) -> datetime | None:
        """Read embedded time from a single file."""
        if self._client is not None:
            return self._client.read_embedded(filepath, use_qt_utc)
        ext = Path(filepath).suffix.lower()
        tags_label = (['-QuickTime:CreateDate', '-QuickTime:MediaCreateDate']
                      if ext in ('.mp4', '.lrv')
                      else ['-EXIF:DateTimeOriginal'])
        args = ['-json']
        if use_qt_utc:
            args += ['-api', 'QuickTimeUTC=1']
        args += tags_label + [str(filepath)]
        raw = self._et.execute(*args)
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
        if self._client is not None:
            return self._client.read_tags_batch(filepaths)
        if not filepaths:
            return {}

        raw = self._et.execute(
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
        if self._client is not None:
            return self._client.read_gps_accuracy_batch(filepaths)
        if not filepaths:
            return {}

        raw = self._et.execute(
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
        if self._client is not None:
            return self._client.write_embedded(path, dt)
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
            self._et.execute('-overwrite_original', *tags, str(path))
            return True
        except Exception:
            return False

    def write_embedded_batch(
        self, pairs: list[tuple[Path, datetime]]
    ) -> bool:
        """Write embedded times for all *pairs*.

        When connected via the server, the request is sent over TCP and
        serialized by the server's single-threaded accept loop.  In
        direct mode (``connect=None``) — only used by the server itself
        — we iterate over pairs without additional locking, since the
        server already handles one request at a time.
        """
        if self._client is not None:
            return self._client.write_embedded_batch(pairs)
        ok = True
        for path, dt in pairs:
            if not self.write_embedded(path, dt):
                ok = False
        return ok

    # ── History dump ───────────────────────────────────────────────────────

    def dump_full_json(self, filepaths: list[Path]) -> str | None:
        """Full exiftool JSON array for a list of files (history log)."""
        if self._client is not None:
            return self._client.dump_full_json(filepaths)
        if not filepaths:
            return None
        raw = self._et.execute('-json', '-G', '-a', '--short',
                                *[str(p) for p in filepaths])
        if raw.strip():
            return raw
        return None

    def dump_tags_json(self, filepaths: list[Path],
                       tags: list[str]) -> str | None:
        """Read specific tags in JSON format (raw output string)."""
        if self._client is not None:
            return self._client.dump_tags_json(filepaths, tags)
        if not filepaths:
            return None
        args = ['-json'] + tags + [str(p) for p in filepaths]
        raw = self._et.execute(*args)
        if raw.strip():
            return raw
        return None
