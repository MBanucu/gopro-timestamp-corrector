"""TCP client for talking to ``ExifToolServer``.

Provides the same method signatures as ``ExifToolSession``, making it a
drop-in replacement.  When the server is not running, auto-spawns it.

Usage::

    client = ExifToolClient()
    available = client.available()
    result = client.read_tags_batch([Path('GH010001.MP4')])
"""

import json
import os
import socket
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from options import EXIFTOOL_SERVER_PORT_FILE


_PORT_FILE = os.path.join(tempfile.gettempdir(), EXIFTOOL_SERVER_PORT_FILE)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    s = dt.isoformat()
    return s


def _from_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _send_request(port: int, method: str,
                   params: dict = None) -> dict:
    """Send a JSON-RPC request to the server and return the response dict.

    Raises ``ConnectionError`` if the server is unreachable.
    """
    if params is None:
        params = {}
    req = json.dumps({'id': 1, 'method': method, 'params': params})
    s = socket.create_connection(('127.0.0.1', port), timeout=10.0)
    try:
        # Long timeout for response — batch writes (exiftool) can take
        # 30-60s+ for many files through a single-threaded server.
        s.settimeout(120.0)
        s.sendall((req + '\n').encode())
        resp = s.makefile('r', encoding='utf-8').readline()
        if not resp:
            raise ConnectionError('Empty response from server')
        data = json.loads(resp.strip())
        if 'error' in data:
            err = data['error']
            raise RuntimeError(
                f'Server error ({err.get("code", -1)}): '
                f'{err.get("message", "unknown")}')
        return data.get('result')
    finally:
        s.close()


def _find_server() -> int:
    """Read the port file and return the server port.

    Raises ``ConnectionError`` if the port file is missing or stale.
    """
    try:
        with open(_PORT_FILE) as f:
            data = json.load(f)
        port = data['port']
        # Quick health check
        _send_request(port, 'ping')
        return port
    except (OSError, json.JSONDecodeError, KeyError, ConnectionError,
            RuntimeError) as exc:
        raise ConnectionError(
            f'Cannot reach exiftool server: {exc}')


def _ensure_server() -> int:
    """Find or auto-spawn the server. Returns its port."""
    try:
        return _find_server()
    except ConnectionError:
        pass

    # Auto-spawn
    from exiftool_server import spawn_server
    return spawn_server(port_file=_PORT_FILE)


class ExifToolClient:
    """Client that talks to ``ExifToolServer`` over TCP.

    All methods match the signatures of ``ExifToolSession`` so this
    can be used as a drop-in replacement.
    """

    def __init__(self):
        self._port = _ensure_server()

    # ── Compatibility with ExifToolSession's context manager ────────

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass  # Don't shut down the server — other clients may use it

    # ── Availability ─────────────────────────────────────────────────

    def available(self) -> bool:
        try:
            return _send_request(self._port, 'available')
        except (ConnectionError, RuntimeError):
            return False

    # ── Single-file reads ───────────────────────────────────────────

    def read_gps_time(self, filepath: str | Path) -> datetime | None:
        result = _send_request(self._port, 'read_gps_time',
                                {'filepath': str(filepath)})
        return _from_iso(result)

    def read_embedded(self, filepath: str | Path,
                      use_qt_utc: bool = True) -> datetime | None:
        result = _send_request(self._port, 'read_embedded',
                                {'filepath': str(filepath),
                                 'use_qt_utc': use_qt_utc})
        return _from_iso(result)

    # ── Batch reads ──────────────────────────────────────────────────

    def read_tags_batch(
        self, filepaths: list[Path]
    ) -> dict[Path, tuple[datetime | None, datetime | None]]:
        paths_str = [str(p) for p in filepaths]
        result = _send_request(self._port, 'read_tags_batch',
                                {'filepaths': paths_str})
        out: dict[Path, tuple[datetime | None, datetime | None]] = {}
        for k, v in result.items():
            out[Path(k)] = (_from_iso(v[0]), _from_iso(v[1]))
        return out

    def read_gps_accuracy_batch(
        self, filepaths: list[Path]
    ) -> dict[Path, float | None]:
        paths_str = [str(p) for p in filepaths]
        result = _send_request(self._port, 'read_gps_accuracy_batch',
                                {'filepaths': paths_str})
        return {Path(k): v for k, v in result.items()}

    # ── Writes ───────────────────────────────────────────────────────

    def write_embedded(self, path: Path, dt: datetime) -> bool:
        return _send_request(self._port, 'write_embedded',
                              {'path': str(path), 'dt': _iso(dt)})

    def write_embedded_batch(
        self, pairs: list[tuple[Path, datetime]]
    ) -> bool:
        serialized = [[str(p), _iso(d)] for p, d in pairs]
        return _send_request(self._port, 'write_embedded_batch',
                              {'pairs': serialized})

    # ── History dump ─────────────────────────────────────────────────

    def dump_full_json(self, filepaths: list[Path]) -> str | None:
        paths_str = [str(p) for p in filepaths]
        return _send_request(self._port, 'dump_full_json',
                              {'filepaths': paths_str})

    def dump_tags_json(self, filepaths: list[Path],
                       tags: list[str]) -> str | None:
        paths_str = [str(p) for p in filepaths]
        return _send_request(self._port, 'dump_tags_json',
                              {'filepaths': paths_str, 'tags': tags})
