"""Tests for server PID election (lower PID wins, concurrent startup).

Extracted from the growing ``test_exiftool_server.py``.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


_BD = str(Path(__file__).resolve().parent.parent / 'src')
if _BD not in sys.path:
    sys.path.insert(0, _BD)

# Include PID so concurrent test invocations (e.g. two `nix run .#test`
# in separate terminals) don't collide on the same port file.
PORT_FILE = os.path.join(
    tempfile.gettempdir(),
    f'gopro-pid-election-test-{os.getpid()}.json')


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
             '--idle-timeout', str(timeout), '--port-file', pf,
             '--no-exiftool'],
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
        """10 servers with staggered launch & election delay — lowest PID wins."""
        pf = PORT_FILE + '.ten_way'
        try:
            os.unlink(pf)
        except OSError:
            pass
        self.addCleanup(self._kill_port_file, pf)
        self.addCleanup(self._clean_log_files, pf)

        N = 5
        procs: list[subprocess.Popen] = []

        for i in range(N):
            launch_ms = i * 10
            target_entry_ms = 100 + (N - 1 - i) * 50
            election_delay_ms = max(10, target_entry_ms - launch_ms)
            log_file = f'{pf}.spawn{i}.log'
            p = subprocess.Popen(
                [sys.executable, os.path.join(_BD, 'exiftool_server.py'),
                 '--idle-timeout', '60', '--port-file', pf,
                 '--log-file', log_file,
                 '--no-exiftool',
                 '--election-delay', str(election_delay_ms / 1000)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            procs.append(p)
            time.sleep(0.01)

        pids = [p.pid for p in procs]
        self.assertEqual(len(set(pids)), N,
                         'Duplicate PIDs — processes not unique')

        # Read log files lazily (only on failure) — by the time a
        # failure happens the servers have had time to write them.
        pids_sorted = sorted(pids)

        def _log_dump():
            lines = []
            for idx, p in enumerate(procs):
                log_file = f'{pf}.spawn{idx}.log'
                pid = p.pid
                try:
                    with open(log_file) as f:
                        content = f.read()
                except OSError as e:
                    lines.append(f'  [{pid}] {log_file}: (not found: {e})')
                    continue
                if not content.strip():
                    lines.append(f'  [{pid}] {log_file}: (empty)')
                    continue
                lines.append(f'  [{pid}] {log_file}:')
                for line in content.rstrip().splitlines():
                    lines.append(f'    {line}')
            return '\n'.join(lines)

        # Now wait for convergence
        result = self._wait_for_converged(pf, expected_pid=min(pids), timeout=15)
        self.assertIsNotNone(result,
            f'Election did not converge to lowest PID {min(pids)}\n'
            f'All PIDs: {pids_sorted}\n'
            f'Server logs:\n{_log_dump()}')
        port, winner_pid = result

        lowest = min(pids)
        if winner_pid != lowest:
            self.fail(
                f'Winner PID {winner_pid} is not the lowest spawned PID {lowest}\n'
                f'All PIDs: {pids_sorted}\n'
                f'Server logs:\n{_log_dump()}')

        deadline = time.monotonic() + 5.0
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

    def _clean_log_files(self, pf: str):
        for fname in os.listdir(tempfile.gettempdir()):
            if fname.startswith(os.path.basename(pf) + '.spawn'):
                try:
                    os.unlink(os.path.join(tempfile.gettempdir(), fname))
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
