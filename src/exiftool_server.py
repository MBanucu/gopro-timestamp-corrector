"""TCP server wrapping a persistent ExifToolSession.

Auto-spawned by ``ExifToolSession(connect='auto')`` on demand.
Runs until idle timeout (60s) or explicit shutdown.
"""

import fcntl
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

from exiftool_protocol import iso, from_iso, ping_server, send_shutdown
from exiftool_session import ExifToolSession
from options import EXIFTOOL_SERVER_PORT_FILE, EXIFTOOL_SERVER_IDLE_TIMEOUT


_PORT_FILE = os.path.join(tempfile.gettempdir(), EXIFTOOL_SERVER_PORT_FILE)
_LOCK_FILE: str | None = None  # derived from _PORT_FILE; set in main()
_IDLE_TIMEOUT = EXIFTOOL_SERVER_IDLE_TIMEOUT
_LOG_FILE: str | None = None


def _get_lock_path() -> str:
    return _LOCK_FILE or _PORT_FILE + '.lock'


def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime('%H:%M:%S.%f')
    line = f'[{ts} exiftool-server] {msg}'
    print(line, file=sys.stderr, flush=True)
    if _LOG_FILE:
        try:
            with open(_LOG_FILE, 'a') as f:
                f.write(line + '\n')
        except OSError:
            pass


def _resolve_port_file() -> tuple[int, int] | None:
    """Read port file and return (port, pid) or None."""
    try:
        with open(_PORT_FILE) as f:
            data = json.load(f)
        return data['port'], data['pid']
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def _write_port_file(port: int, pid: int):
    """Atomically write port file (via atomic rename)."""
    data = {
        'port': port, 'pid': pid,
        'started_at': datetime.now(timezone.utc).isoformat(),
    }
    tmp = _PORT_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f)
        f.flush()
        os.fsync(f.fileno())
    os.rename(tmp, _PORT_FILE)


def _takeover_or_exit(port: int, pid: int) -> bool:
    """Claim the port file via PID election (flock-based mutual exclusion).

    Locks a dedicated ``.lock`` file (never deleted), then reads the
    current port file contents.  If this process has a lower PID than
    the existing winner it writes itself as the winner *before* sending
    shutdown — this ensures the old server's ``_clean_port_file`` reads
    the new winner's PID and does NOT delete the port file out from
    under us.
    """
    lock_path = _get_lock_path()
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        content = None
        try:
            with open(_PORT_FILE) as f:
                content = f.read().strip()
        except OSError:
            pass

        if content:
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                data = None

            if data and 'pid' in data and 'port' in data:
                e_pid, e_port = data['pid'], data['port']

                if e_pid == pid:
                    _log(f'pid {pid}: already recorded as winner')
                    return True

                if ping_server(e_port, timeout=1.0):
                    if pid > e_pid:
                        _log(f'pid {pid}: alive server pid={e_pid} < our pid, exiting')
                        return False
                    _log(f'pid {pid}: pid < existing {e_pid}, taking over')
                    # Write ourselves as winner FIRST so that the old
                    # server's _clean_port_file sees our PID and doesn't
                    # unlink the file while we hold the old inode.
                    _write_port_file(port, pid)
                    _log(f'pid {pid}: sending shutdown to pid {e_pid} at port {e_port}')
                    send_shutdown(e_port)
                    time.sleep(0.2)
                    return True
                else:
                    _log(f'pid {pid}: existing pid={e_pid} stale')
            else:
                _log(f'pid {pid}: corrupt port file, overwriting')

        _write_port_file(port, pid)
        _log(f'pid {pid}: wrote port file, continuing as server')
        return True
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


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

    log_file = port_file.rsplit('.', 1)[0] + '.log'
    proc = subprocess.Popen(
        [sys.executable, __file__, '--port-file', port_file,
         '--log-file', log_file],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with open(port_file) as f:
                data = json.load(f)
            port = data['port']
            if ping_server(port):
                return port
        except (OSError, json.JSONDecodeError, KeyError, ConnectionError):
            pass
        time.sleep(0.05)

    # Timed out
    try:
        proc.terminate()
    except OSError:
        pass
    log_path = port_file.rsplit('.', 1)[0] + '.log'
    raise RuntimeError(
        f'ExifTool server did not start within {timeout}s '
        f'(check logs at {log_path})')


class ExifToolServer:
    """TCP server wrapping an ``ExifToolSession``.

    Listens on ``127.0.0.1:0`` (random port) and handles
    newline-delimited JSON requests.  Auto-shuts down after
    *idle_timeout* seconds of inactivity.
    """

    def __init__(self, idle_timeout: int = _IDLE_TIMEOUT,
                 election_delay: float = 0.0, *,
                 no_exiftool: bool = False):
        self.session = None if no_exiftool else ExifToolSession(connect=None)
        self.idle_timeout = idle_timeout
        self._election_delay = election_delay
        self._last_request_time = time.monotonic()
        self._server: socket.socket | None = None
        self._active = True
        self._port = 0
        self._election_result: bool | None = None

    # ── Lifecycle ────────────────────────────────────────────────────

    def serve(self) -> int:
        """Start serving. Returns the port number.

        The election runs in a background thread so the accept loop
        can respond to health-check pings during the election —
        previously a competing server inside its own election would
        hang on ``ping_server`` because this server hadn't entered
        ``accept()`` yet.
        """
        if self.session:
            self.session.__enter__()
        try:
            self._bind()
            self._server.listen(10)
            self._server.settimeout(1.0)
            self._run_election_async()

            self._setup_signal_handlers()
            self._start_watchdog()

            while self._active:
                try:
                    conn, addr = self._server.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                self._handle_connection(conn)

            # Election finished — check result
            if self._election_result is False:
                self._server.close()
                if self.session:
                    self.session.__exit__(None, None, None)
                return 0  # Another server won the election
        finally:
            _clean_port_file(os.getpid())
            if self.session:
                self.session.__exit__(None, None, None)
            self._log('Server stopped')
        return self._port

    def _run_election_async(self):
        """Start the PID election in a daemon thread.

        The accept loop is already running, so health-check pings
        from competing servers are answered promptly even while this
        server holds the flock.
        """
        def _run():
            if self._election_delay:
                time.sleep(self._election_delay)
            self._election_result = _takeover_or_exit(
                self._port, os.getpid())
            if self._election_result:
                self._log(f'Server started on 127.0.0.1:{self._port} '
                          f'(pid={os.getpid()})')
            else:
                self._log('Lost election, shutting down')
                self.stop()

        t = threading.Thread(target=_run, daemon=True)
        t.start()

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
                peer = conn.getpeername()
                resp = self._dispatch(line.strip(), peer)
                conn.sendall((resp + '\n').encode())
            except (OSError, socket.timeout):
                pass
            finally:
                self._last_request_time = time.monotonic()

    def _dispatch(self, line: str,
                  peer: tuple[str, int] | None = None) -> str:
        """Parse JSON-RPC request and return JSON response string."""
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            self._log(f'bad json: {e}')
            return json.dumps(
                {'id': None, 'error': {'code': -32700, 'message': str(e)}})

        method = req.get('method', '')
        params = req.get('params', {})
        req_id = req.get('id')

        if method == 'ping':
            who = f'{peer[0]}:{peer[1]}' if peer else '?'
            self._log(f'ping from {who}')
        elif method == 'shutdown':
            who = f'{peer[0]}:{peer[1]}' if peer else '?'
            self._log(f'shutdown from {who}')

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
            return iso(self.session.read_gps_time(filepath))
        except Exception:
            return None

    def _method_read_embedded(self, filepath: str,
                              use_qt_utc: bool = True) -> str | None:
        try:
            return iso(self.session.read_embedded(filepath, use_qt_utc))
        except Exception:
            return None

    def _method_read_tags_batch(
            self, filepaths: list[str]
    ) -> dict[str, list[str | None]]:
        try:
            paths = [Path(p) for p in filepaths]
            result = self.session.read_tags_batch(paths)
            return {
                str(k): [iso(v[0]), iso(v[1])]
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
            return self.session.write_embedded(Path(path), from_iso(dt))
        except Exception:
            return False

    def _method_write_embedded_batch(self, pairs: list) -> bool:
        try:
            parsed = [(Path(p), from_iso(d)) for p, d in pairs]
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
        _log(msg)


def main():
    """CLI entry point for the server."""
    import argparse
    global _PORT_FILE, _IDLE_TIMEOUT, _LOG_FILE
    parser = argparse.ArgumentParser(
        description='ExifTool server daemon')
    parser.add_argument('--idle-timeout', type=int, default=_IDLE_TIMEOUT,
                        help=f'Idle timeout in seconds (default: {_IDLE_TIMEOUT})')
    parser.add_argument('--port-file', default=_PORT_FILE,
                        help=f'Path to port file (default: {_PORT_FILE})')
    parser.add_argument('--log-file',
                        help='Path to log file (in addition to stderr)')
    parser.add_argument('--election-delay', type=float, default=0.0,
                        help='Seconds to wait before PID election (testing)')
    parser.add_argument('--no-exiftool', action='store_true',
                        help='Skip starting exiftool (testing only)')
    args = parser.parse_args()

    _PORT_FILE = args.port_file
    _LOCK_FILE = args.port_file + '.lock'
    _IDLE_TIMEOUT = args.idle_timeout
    if args.log_file:
        _LOG_FILE = args.log_file

    server = ExifToolServer(idle_timeout=args.idle_timeout,
                            election_delay=args.election_delay,
                            no_exiftool=args.no_exiftool)
    sys.exit(server.serve())


if __name__ == '__main__':
    main()
