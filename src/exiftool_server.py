"""TCP server wrapping a persistent ExifToolSession.

Auto-spawned by ``ExifToolSession(connect='auto')`` on demand.
Runs until idle timeout (60s) or explicit shutdown.
"""

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from exiftool_session import ExifToolSession
from options import EXIFTOOL_SERVER_PORT_FILE, EXIFTOOL_SERVER_IDLE_TIMEOUT


_PORT_FILE = os.path.join(tempfile.gettempdir(), EXIFTOOL_SERVER_PORT_FILE)
_IDLE_TIMEOUT = EXIFTOOL_SERVER_IDLE_TIMEOUT


def _iso(dt: datetime | None) -> str | None:
    """Serialize a datetime to ISO 8601 string (or None)."""
    if dt is None:
        return None
    return dt.isoformat()


def _from_iso(s: str | None) -> datetime | None:
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


def _resolve_port_file() -> tuple[int, int] | None:
    """Read port file and return (port, pid) or None."""
    try:
        with open(_PORT_FILE) as f:
            data = json.load(f)
        return data['port'], data['pid']
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def _ping_server(port: int, timeout: float = 2.0) -> bool:
    """Check if a server at *port* is alive by sending ping."""
    try:
        s = socket.create_connection(('127.0.0.1', port), timeout=timeout)
        req = json.dumps({'id': 1, 'method': 'ping', 'params': {}})
        s.sendall((req + '\n').encode())
        resp = s.makefile('r', encoding='utf-8').readline()
        s.close()
        if resp:
            data = json.loads(resp.strip())
            return data.get('result') == 'pong'
        return False
    except (ConnectionRefusedError, OSError, json.JSONDecodeError, socket.timeout):
        return False


def _send_shutdown(port: int, timeout: float = 2.0) -> bool:
    """Send shutdown command to server at *port*."""
    try:
        s = socket.create_connection(('127.0.0.1', port), timeout=timeout)
        req = json.dumps({'id': 1, 'method': 'shutdown', 'params': {}})
        s.sendall((req + '\n').encode())
        s.close()
        return True
    except (ConnectionRefusedError, OSError, socket.timeout):
        return False


def _takeover_or_exit(port: int, pid: int) -> bool:
    """Claim the port file via PID election.

    Returns True if this process should continue as the server,
    False if it should exit (existing server with lower PID wins).
    """
    for _ in range(5):
        try:
            fd = os.open(_PORT_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, 'w') as f:
                json.dump({'port': port, 'pid': pid,
                           'started_at': datetime.now(timezone.utc).isoformat()}, f)
            return True

        existing = _resolve_port_file()
        if existing is None:
            try:
                os.unlink(_PORT_FILE)
            except OSError:
                pass
            continue

        e_port, e_pid = existing
        if _ping_server(e_port):
            if pid < e_pid:
                _send_shutdown(e_port)
                time.sleep(0.2)
                try:
                    os.unlink(_PORT_FILE)
                except OSError:
                    pass
                continue
            else:
                return False  # existing server (lower PID) wins
        else:
            try:
                os.unlink(_PORT_FILE)
            except OSError:
                pass
            continue

    return False


def _get_port_file() -> str:
    return _PORT_FILE


def _clean_port_file(pid: int):
    """Remove port file if it belongs to *pid*."""
    try:
        existing = _resolve_port_file()
        if existing and existing[1] == pid:
            os.unlink(_PORT_FILE)
    except OSError:
        pass


def spawn_server(timeout: float = 5.0,
                 port_file: str | None = None) -> int:
    """Spawn the server as a background subprocess and return its port.

    Blocks until the server is ready (port file appears) or *timeout*.
    Raises RuntimeError if the server doesn't start in time.
    """
    if port_file is None:
        port_file = _PORT_FILE

    # Delete stale port file first
    try:
        os.unlink(port_file)
    except OSError:
        pass

    proc = subprocess.Popen(
        [sys.executable, __file__, '--port-file', port_file],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with open(port_file) as f:
                data = json.load(f)
            port = data['port']
            if _ping_server(port):
                return port
        except (OSError, json.JSONDecodeError, KeyError, ConnectionError):
            pass
        time.sleep(0.05)

    # Timed out
    try:
        proc.terminate()
    except OSError:
        pass
    raise RuntimeError(
        f'ExifTool server did not start within {timeout}s')


class ExifToolServer:
    """TCP server wrapping an ``ExifToolSession``.

    Listens on ``127.0.0.1:0`` (random port) and handles
    newline-delimited JSON requests.  Auto-shuts down after
    *idle_timeout* seconds of inactivity.
    """

    def __init__(self, idle_timeout: int = _IDLE_TIMEOUT):
        self.session = ExifToolSession(connect=None)
        self.idle_timeout = idle_timeout
        self._last_request_time = time.monotonic()
        self._server: socket.socket | None = None
        self._active = True
        self._port = 0

    # ── Lifecycle ────────────────────────────────────────────────────

    def serve(self) -> int:
        """Start serving. Returns the port number.

        Blocks until shutdown is requested (idle timeout, shutdown
        method, or signal).
        """
        self.session.__enter__()
        try:
            self._bind()
            if not _takeover_or_exit(self._port, os.getpid()):
                self.session.__exit__(None, None, None)
                return 0  # Another server won the election

            self._setup_signal_handlers()
            self._start_watchdog()

            self._server.listen(5)
            self._server.settimeout(1.0)
            self._log(f'Server started on 127.0.0.1:{self._port} '
                      f'(pid={os.getpid()})')

            while self._active:
                try:
                    conn, addr = self._server.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                self._handle_connection(conn)
        finally:
            _clean_port_file(os.getpid())
            self.session.__exit__(None, None, None)
            self._log('Server stopped')
        return self._port

    def stop(self):
        """Signal the server loop to stop."""
        self._active = False

    @property
    def port(self) -> int:
        return self._port

    # ── Private: connection handling ─────────────────────────────────

    def _bind(self):
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(('127.0.0.1', 0))
        self._port = self._server.getsockname()[1]

    def _handle_connection(self, conn: socket.socket):
        with conn:
            conn.settimeout(30.0)
            try:
                reader = conn.makefile('r', encoding='utf-8')
                line = reader.readline()
                if not line:
                    return
                resp = self._dispatch(line.strip())
                conn.sendall((resp + '\n').encode())
            except (OSError, socket.timeout):
                pass
            finally:
                self._last_request_time = time.monotonic()

    def _dispatch(self, line: str) -> str:
        """Parse JSON-RPC request and return JSON response string."""
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            return json.dumps(
                {'id': None, 'error': {'code': -32700, 'message': str(e)}})

        method = req.get('method', '')
        params = req.get('params', {})
        req_id = req.get('id')

        handler = getattr(self, f'_method_{method}', None)
        if handler is None:
            return json.dumps(
                {'id': req_id,
                 'error': {'code': -32601,
                           'message': f'Unknown method: {method}'}})

        try:
            result = handler(**params)
            return json.dumps({'id': req_id, 'result': result})
        except Exception as e:
            return json.dumps(
                {'id': req_id,
                 'error': {'code': -32603, 'message': f'{type(e).__name__}: {e}'}})

    # ── RPC methods ──────────────────────────────────────────────────

    def _method_ping(self) -> str:
        return 'pong'

    def _method_status(self) -> dict:
        return {
            'port': self._port,
            'pid': os.getpid(),
            'uptime': time.monotonic() - self._last_request_time,
            'idle_timeout': self.idle_timeout,
        }

    def _method_shutdown(self) -> str:
        def _delayed_stop():
            time.sleep(0.1)
            self.stop()
        threading.Thread(target=_delayed_stop, daemon=True).start()
        return 'shutting_down'

    def _method_available(self) -> bool:
        return self.session.available()

    def _method_read_gps_time(self, filepath: str) -> str | None:
        try:
            return _iso(self.session.read_gps_time(filepath))
        except Exception:
            return None

    def _method_read_embedded(self, filepath: str,
                              use_qt_utc: bool = True) -> str | None:
        try:
            return _iso(self.session.read_embedded(filepath, use_qt_utc))
        except Exception:
            return None

    def _method_read_tags_batch(
            self, filepaths: list[str]
    ) -> dict[str, list[str | None]]:
        try:
            paths = [Path(p) for p in filepaths]
            result = self.session.read_tags_batch(paths)
            return {
                str(k): [_iso(v[0]), _iso(v[1])]
                for k, v in result.items()
            }
        except Exception:
            return {}

    def _method_read_gps_accuracy_batch(
            self, filepaths: list[str]
    ) -> dict[str, float | None]:
        try:
            paths = [Path(p) for p in filepaths]
            result = self.session.read_gps_accuracy_batch(paths)
            return {str(k): v for k, v in result.items()}
        except Exception:
            return {}

    def _method_write_embedded(self, path: str, dt: str) -> bool:
        try:
            return self.session.write_embedded(Path(path), _from_iso(dt))
        except Exception:
            return False

    def _method_write_embedded_batch(self, pairs: list) -> bool:
        try:
            parsed = [(Path(p), _from_iso(d)) for p, d in pairs]
            return self.session.write_embedded_batch(parsed)
        except Exception:
            return False

    def _method_dump_full_json(self, filepaths: list[str]) -> str | None:
        try:
            paths = [Path(p) for p in filepaths]
            return self.session.dump_full_json(paths)
        except Exception:
            return None

    def _method_dump_tags_json(self, filepaths: list[str],
                                tags: list[str]) -> str | None:
        try:
            paths = [Path(p) for p in filepaths]
            return self.session.dump_tags_json(paths, tags)
        except Exception:
            return None

    # ── Watchdog ─────────────────────────────────────────────────────

    def _start_watchdog(self):
        interval = max(1.0, self.idle_timeout / 4)

        def _watch():
            while self._active:
                time.sleep(interval)
                if not self._active:
                    break
                elapsed = time.monotonic() - self._last_request_time
                if elapsed > self.idle_timeout:
                    self._log(
                        f'Idle {elapsed:.0f}s > {self.idle_timeout}s, '
                        f'shutting down')
                    self.stop()

        t = threading.Thread(target=_watch, daemon=True)
        t.start()

    def _setup_signal_handlers(self):
        if threading.current_thread() is not threading.main_thread():
            return
        def _handler(signum, frame):
            self.stop()

        if sys.platform != 'win32':
            signal.signal(signal.SIGTERM, _handler)
            signal.signal(signal.SIGINT, _handler)

    def _log(self, msg: str):
        print(f'[exiftool-server] {msg}', file=sys.stderr)


def main():
    """CLI entry point for the server."""
    import argparse
    global _PORT_FILE, _IDLE_TIMEOUT
    parser = argparse.ArgumentParser(
        description='ExifTool server daemon')
    parser.add_argument('--idle-timeout', type=int, default=_IDLE_TIMEOUT,
                        help=f'Idle timeout in seconds (default: {_IDLE_TIMEOUT})')
    parser.add_argument('--port-file', default=_PORT_FILE,
                        help=f'Path to port file (default: {_PORT_FILE})')
    args = parser.parse_args()

    _PORT_FILE = args.port_file
    _IDLE_TIMEOUT = args.idle_timeout

    server = ExifToolServer(idle_timeout=args.idle_timeout)
    sys.exit(server.serve())


if __name__ == '__main__':
    main()
