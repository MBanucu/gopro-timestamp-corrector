"""Tests for server PID election (lower PID wins, concurrent startup).

Extracted from the growing ``test_exiftool_server.py``.
"""
import fcntl
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


_BD = str(Path(__file__).resolve().parent.parent / 'src')
if _BD not in sys.path:
    sys.path.insert(0, _BD)

# Unique port file — must not collide with test_exiftool_server.py.
PORT_FILE = os.path.join(tempfile.gettempdir(),
                          'gopro-pid-election-test.json')


def _clean_port_file():
    try:
        os.unlink(PORT_FILE)
    except OSError:
        pass


def _send(port: int, method: str, params: dict = None,
          timeout: float = 5.0) -> dict:
    """Send a JSON-RPC request and return the response dict."""
    if params is None:
        params = {}
    s = socket.create_connection(('127.0.0.1', port), timeout=timeout)
    try:
        req = json.dumps({'id': 1, 'method': method, 'params': params})
        s.sendall((req + '\n').encode())
        resp = s.makefile('r', encoding='utf-8').readline()
        if not resp:
            raise ConnectionError('Empty response')
        return json.loads(resp.strip())
    finally:
        s.close()


class TestPidElection(unittest.TestCase):
    """Test that when multiple servers start, the lowest PID wins."""

    def setUp(self):
        _clean_port_file()

    def tearDown(self):
        _clean_port_file()

    def _start_server(self, pf: str, timeout: int = 10) -> subprocess.Popen:
        proc = subprocess.Popen(
            [sys.executable, os.path.join(_BD, 'exiftool_server.py'),
             '--idle-timeout', str(timeout), '--port-file', pf],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return proc

    def _wait_for_port(self, pf: str, timeout: float = 5) -> int | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with open(pf) as f:
                    data = json.load(f)
                if _send(data['port'], 'ping').get('result') == 'pong':
                    return data['port']
            except (OSError, json.JSONDecodeError, KeyError,
                    ConnectionError):
                pass
            time.sleep(0.05)
        return None

    def test_lower_pid_wins(self):
        """Start server A (lower PID expected), then server B.
        B should detect A is alive and exit.
        """
        pf = PORT_FILE + '.election'
        try:
            os.unlink(pf)
        except OSError:
            pass

        proc_a = self._start_server(pf, timeout=10)
        port_a = self._wait_for_port(pf)
        self.assertIsNotNone(port_a)

        with open(pf) as f:
            data_a = json.load(f)
        pid_a = data_a['pid']

        proc_b = self._start_server(pf, timeout=10)

        try:
            proc_b.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc_b.kill()
            self.fail('Server B did not exit after detecting existing server')

        self.assertTrue(
            _send(port_a, 'ping').get('result') == 'pong',
            'Server A should still be running')

        _send(port_a, 'shutdown')
        proc_a.wait(timeout=3)
        try:
            os.unlink(pf)
        except OSError:
            pass

    def _wait_for_converged(self, pf: str, expected_pid: int | None = None,
                            timeout: float = 10) -> tuple[int, int] | None:
        """Poll port file until the PID is stable *and* the server responds.

        When *expected_pid* is given, also waits until the port file PID
        matches it (ensuring the lowest-PID server has taken over).

        Returns ``(port, winner_pid)`` or ``None`` on timeout.
        """
        deadline = time.monotonic() + timeout
        prev_pid = None
        while time.monotonic() < deadline:
            try:
                with open(pf) as f:
                    data = json.load(f)
                port = data['port']
                pid = data['pid']
                match = (pid == prev_pid and
                         _send(port, 'ping').get('result') == 'pong')
                if match and (expected_pid is None or pid == expected_pid):
                    return port, pid
                prev_pid = pid
            except (OSError, json.JSONDecodeError, KeyError, ConnectionError):
                pass
            time.sleep(0.2)
        return None

    def test_ten_servers_concurrent_lowest_pid_wins(self):
        """10 servers started concurrently via Popen — lowest PID wins."""
        pf = PORT_FILE + '.ten_way'
        try:
            os.unlink(pf)
        except OSError:
            pass
        self.addCleanup(self._kill_port_file, pf)

        N = 10
        procs: list[subprocess.Popen] = []
        lock = threading.Lock()
        barrier = threading.Barrier(N, timeout=15)
        exc_info: list[Exception] = []

        def spawn():
            try:
                barrier.wait()
                p = subprocess.Popen(
                    [sys.executable, os.path.join(_BD, 'exiftool_server.py'),
                     '--idle-timeout', '30', '--port-file', pf],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                with lock:
                    procs.append(p)
            except Exception as e:
                with lock:
                    exc_info.append(e)

        threads = [threading.Thread(target=spawn) for _ in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        if exc_info:
            self.fail(f'{len(exc_info)} threads raised exceptions')

        self.assertEqual(len(procs), N,
                         f'Expected {N} procs, got {len(procs)}')

        pids = [p.pid for p in procs]
        self.assertEqual(len(set(pids)), N,
                         'Duplicate PIDs — processes not unique')

        result = self._wait_for_converged(pf, expected_pid=min(pids), timeout=10)
        self.assertIsNotNone(result,
                             f'Election did not converge to lowest PID {min(pids)}')
        port, winner_pid = result

        # Build log dump from all processes' stderr (non-blocking read)
        logs = {}
        pids_sorted = sorted(pids)
        for p in procs:
            if not p.stderr:
                continue
            # Make the pipe non-blocking so we don't hang on still-running
            # survivors (the winner exits via idle-timeout *after* the test).
            fd = p.stderr.fileno()
            fl = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
            try:
                raw = p.stderr.read()
                if raw:
                    out = raw.decode('utf-8', errors='replace').strip()
                    if out:
                        logs[p.pid] = out
            except (BlockingIOError, ValueError):
                pass
            p.stderr.close()

        def _log_dump():
            lines = []
            for pid in pids_sorted:
                if pid in logs:
                    for line in logs[pid].splitlines():
                        lines.append(f'  [{pid}] {line}')
            return '\n'.join(lines)

        lowest = min(pids)
        if winner_pid != lowest:
            self.fail(
                f'Winner PID {winner_pid} is not the lowest spawned PID {lowest}\n'
                f'All PIDs: {pids_sorted}\n'
                f'Server logs:\n{_log_dump()}')

        deadline = time.monotonic() + 3.0
        alive = []
        while time.monotonic() < deadline:
            alive = [p for p in procs if p.poll() is None]
            if len(alive) <= 1:
                break
            time.sleep(0.2)
        if len(alive) != 1:
            self.fail(
                f'Expected 1 survivor, got {len(alive)}: '
                f'{[a.pid for a in alive]}\n'
                f'Server logs:\n{_log_dump()}')

        self.assertEqual(alive[0].pid, winner_pid,
                         'Alive PID does not match port-file PID')

        self.assertEqual(
            _send(port, 'ping').get('result'), 'pong',
            'Surviving server not responding')

        try:
            _send(port, 'shutdown')
        except ConnectionError:
            pass
        for p in procs:
            self._kill_proc(p)

    def _kill_port_file(self, pf: str):
        try:
            os.unlink(pf)
        except OSError:
            pass

    def _kill_proc(self, p: subprocess.Popen):
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait(timeout=2)
        except OSError:
            pass


if __name__ == '__main__':
    unittest.main()
