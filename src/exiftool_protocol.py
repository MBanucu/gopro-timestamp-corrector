"""Shared protocol utilities for the ExifTool TCP server and client.

Provides:
  - ``iso()`` / ``from_iso()`` — datetime serialization used by RPC methods
  - ``ping_server()`` / ``send_shutdown()`` — low-level TCP helpers
"""

import json
import os
import socket
from datetime import datetime, timezone


def iso(dt: datetime | None) -> str | None:
    """Serialize a datetime to ISO 8601 string (or None)."""
    if dt is None:
        return None
    return dt.isoformat()


def from_iso(s: str | None) -> datetime | None:
    """Parse an ISO 8601 string back to a UTC-aware datetime."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def ping_server(port: int, timeout: float = 2.0) -> bool:
    """Check if a server at *port* is alive by sending ping."""
    try:
        s = socket.create_connection(('127.0.0.1', port), timeout=timeout)
        s.settimeout(timeout)
        req = json.dumps({'id': 1, 'method': 'ping', 'params': {}})
        s.sendall((req + '\n').encode())
        resp = s.makefile('r', encoding='utf-8').readline()
        s.close()
        if resp:
            data = json.loads(resp.strip())
            ok = data.get('result') == 'pong'
            return ok
        return False
    except (ConnectionRefusedError, OSError, json.JSONDecodeError, socket.timeout) as exc:
        return False


def send_shutdown(port: int, timeout: float = 2.0) -> bool:
    """Send shutdown command to server at *port*."""
    try:
        s = socket.create_connection(('127.0.0.1', port), timeout=timeout)
        req = json.dumps({'id': 1, 'method': 'shutdown', 'params': {}})
        s.sendall((req + '\n').encode())
        s.close()
        return True
    except (ConnectionRefusedError, OSError, socket.timeout):
        return False
