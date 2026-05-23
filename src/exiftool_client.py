"""TCP client for talking to ``ExifToolServer``.

Provides the same method signatures as ``ExifToolSession``, making it a
drop-in replacement.  When the server is not running, auto-spawns it.

Usage::

    client = ExifToolClient()
    available = client.available()
    result = client.read_tags_batch([Path('GH010001.MP4')])
"""

import fcntl
import json
import os
import socket
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from exiftool_protocol import iso, from_iso
from options import EXIFTOOL_SERVER_PORT_FILE


_PORT_FILE = os.path.join(tempfile.gettempdir(), EXIFTOOL_SERVER_PORT_FILE)


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


def _find_server(port_file: str | None = None) -> int:
    """Read *port_file* and return the server port.

    Raises ``ConnectionError`` if the file is missing or the server stale.
    Defaults to ``_PORT_FILE``.
    """
    if port_file is None:
        port_file = _PORT_FILE
    try:
        with open(port_file) as f:
            data = json.load(f)
        port = data['port']
        _send_request(port, 'ping')
        return port
    except (OSError, json.JSONDecodeError, KeyError, ConnectionError,
            RuntimeError) as exc:
        raise ConnectionError(
            f'Cannot reach exiftool server: {exc}')


def _ensure_server(port_file: str | None = None) -> int:
    """Find or auto-spawn the server. Returns its port.

    Uses double-checked locking with an exclusive ``flock`` to prevent
    concurrent callers from both trying to spawn a server.
    """
    if port_file is None:
        port_file = _PORT_FILE
    # Client-side lock.  Must NOT match the server's lock path
    # (``port_file + '.lock'``) or the server's ``_takeover_or_exit``
    # would deadlock while the client holds this lock.
    lock_file = port_file + '.client.lock'

    try:
        return _find_server(port_file=port_file)
    except ConnectionError:
        pass

    with open(lock_file, 'w') as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        try:
            return _find_server(port_file=port_file)
        except ConnectionError:
            pass

        from exiftool_server import spawn_server
        return spawn_server(port_file=port_file)


class ExifToolClient:
    """Client that talks to ``ExifToolServer`` over TCP.

    All methods match the signatures of ``ExifToolSession`` so this
    can be used as a drop-in replacement.

    Args:
        port_file: Path to the server's port file.  When set, the client
            uses the server described by this file (or auto-spawns one
            if the file doesn't exist).  Defaults to the global
            ``_PORT_FILE``.
    """

    def __init__(self, port_file: str | None = None):
        if port_file is None:
            port_file = _PORT_FILE
        self._port_file = port_file
        self._port = _ensure_server(port_file=port_file)

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
        return from_iso(result)

    def read_embedded(self, filepath: str | Path,
                      use_qt_utc: bool = True) -> datetime | None:
        result = _send_request(self._port, 'read_embedded',
                                {'filepath': str(filepath),
                                 'use_qt_utc': use_qt_utc})
        return from_iso(result)

    # ── Batch reads ──────────────────────────────────────────────────

    def read_tags_batch(
        self, filepaths: list[Path]
    ) -> dict[Path, tuple[datetime | None, datetime | None]]:
        paths_str = [str(p) for p in filepaths]
        result = _send_request(self._port, 'read_tags_batch',
                                {'filepaths': paths_str})
        out: dict[Path, tuple[datetime | None, datetime | None]] = {}
        for k, v in result.items():
            out[Path(k)] = (from_iso(v[0]), from_iso(v[1]))
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
                              {'path': str(path), 'dt': iso(dt)})

    def write_embedded_batch(
        self, pairs: list[tuple[Path, datetime]]
    ) -> bool:
        serialized = [[str(p), iso(d)] for p, d in pairs]
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
