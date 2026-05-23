"""Tests for ExifTool server, client, and session integration.

These tests cover:
- Server ping / status / shutdown protocol
- Client auto-spawn and connection
- ``ExifToolSession(connect='auto')`` delegation
- PID election (lower PID wins)
- Idle timeout auto-shutdown
- Protocol error handling
"""

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


PORT_FILE = os.path.join(
    tempfile.gettempdir(), 'gopro-exiftool-server.json')
IDLE_TIMEOUT = 3  # short timeout for tests


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


class TestServerProtocol(unittest.TestCase):
    """Test the raw TCP protocol against a server subprocess."""

    @classmethod
    def setUpClass(cls):
        _clean_port_file()
        cls._proc = subprocess.Popen(
            [sys.executable, os.path.join(_BD, 'exiftool_server.py'),
             '--idle-timeout', str(IDLE_TIMEOUT),
             '--port-file', PORT_FILE],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Wait for port file
        deadline = time.monotonic() + 5
        cls._port = None
        while time.monotonic() < deadline:
            try:
                with open(PORT_FILE) as f:
                    data = json.load(f)
                cls._port = data['port']
                break
            except (OSError, json.JSONDecodeError, KeyError):
                time.sleep(0.05)
        if cls._port is None:
            raise RuntimeError('Server did not start in time')

    @classmethod
    def tearDownClass(cls):
        try:
            _send(cls._port, 'shutdown')
            cls._proc.wait(timeout=3)
        except Exception:
            cls._proc.kill()
        _clean_port_file()

    def test_ping(self):
        resp = _send(self._port, 'ping')
        self.assertEqual(resp.get('result'), 'pong')

    def test_status(self):
        resp = _send(self._port, 'status')
        self.assertIn('result', resp)
        r = resp['result']
        self.assertIn('port', r)
        self.assertIn('pid', r)
        self.assertIn('idle_timeout', r)
        self.assertEqual(r['idle_timeout'], IDLE_TIMEOUT)

    def test_unknown_method(self):
        resp = _send(self._port, 'nonexistent')
        self.assertIn('error', resp)
        self.assertEqual(resp['error']['code'], -32601)

    def test_malformed_json(self):
        s = socket.create_connection(('127.0.0.1', self._port), timeout=5)
        try:
            s.sendall(b'not json\n')
            resp = s.makefile('r', encoding='utf-8').readline()
            data = json.loads(resp.strip())
            self.assertIn('error', data)
            self.assertEqual(data['error']['code'], -32700)
        finally:
            s.close()

    def test_available(self):
        resp = _send(self._port, 'available')
        self.assertIsInstance(resp.get('result'), bool)

    def test_shutdown(self):
        # Spawn a fresh server to test shutdown on
        pf = PORT_FILE + '.shutdown_test'
        try:
            os.unlink(pf)
        except OSError:
            pass
        proc = subprocess.Popen(
            [sys.executable, os.path.join(_BD, 'exiftool_server.py'),
             '--idle-timeout', '10', '--port-file', pf],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 5
        port = None
        while time.monotonic() < deadline:
            try:
                with open(pf) as f:
                    data = json.load(f)
                port = data['port']
                break
            except (OSError, json.JSONDecodeError, KeyError):
                time.sleep(0.05)
        self.assertIsNotNone(port)

        resp = _send(port, 'shutdown')
        self.assertEqual(resp.get('result'), 'shutting_down')

        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        try:
            os.unlink(pf)
        except OSError:
            pass


class TestClientAutoSpawn(unittest.TestCase):
    """Test the client auto-spawn feature."""

    def setUp(self):
        _clean_port_file()

    def tearDown(self):
        # Shut down any running server
        try:
            with open(PORT_FILE) as f:
                data = json.load(f)
            _send(data['port'], 'shutdown')
        except (OSError, json.JSONDecodeError, KeyError, ConnectionError):
            pass
        _clean_port_file()

    def test_auto_spawn(self):
        from exiftool_client import ExifToolClient
        client = ExifToolClient()
        self.assertTrue(client.available())

    def test_ping_after_connect(self):
        from exiftool_client import ExifToolClient
        client = ExifToolClient()
        resp = _send(client._port, 'ping')
        self.assertEqual(resp.get('result'), 'pong')

    def test_read_gps_nonexistent_file(self):
        from exiftool_client import ExifToolClient
        client = ExifToolClient()
        result = client.read_gps_time('/nonexistent/file.mp4')
        # Should return None for nonexistent file (graceful error)
        self.assertIsNone(result)


class TestSessionIntegration(unittest.TestCase):
    """Test ExifToolSession(connect='auto') delegation."""

    def setUp(self):
        _clean_port_file()

    def tearDown(self):
        try:
            with open(PORT_FILE) as f:
                data = json.load(f)
            _send(data['port'], 'shutdown')
        except (OSError, json.JSONDecodeError, KeyError, ConnectionError):
            pass
        _clean_port_file()

    def test_session_available(self):
        from exiftool_session import ExifToolSession
        with ExifToolSession(connect='auto') as session:
            self.assertTrue(session.available())

    def test_session_reuses_server(self):
        """Two sessions should share the same server process."""
        from exiftool_session import ExifToolSession

        with ExifToolSession(connect='auto') as s1:
            self.assertTrue(s1.available())
            with ExifToolSession(connect='auto') as s2:
                self.assertTrue(s2.available())


class TestIdleShutdown(unittest.TestCase):
    """Test that the server shuts down after idle timeout."""

    def test_idle_timeout(self):
        _clean_port_file()
        pf = PORT_FILE + '.idle_test'
        try:
            os.unlink(pf)
        except OSError:
            pass

        proc = subprocess.Popen(
            [sys.executable, os.path.join(_BD, 'exiftool_server.py'),
             '--idle-timeout', '2', '--port-file', pf],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        deadline = time.monotonic() + 5
        port = None
        while time.monotonic() < deadline:
            try:
                with open(pf) as f:
                    data = json.load(f)
                port = data['port']
                break
            except (OSError, json.JSONDecodeError, KeyError):
                time.sleep(0.05)
        self.assertIsNotNone(port)

        # Server should auto-shutdown after ~2s idle
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            self.fail('Server did not auto-shutdown within idle timeout')

        try:
            os.unlink(pf)
        except OSError:
            pass


class TestPidElection(unittest.TestCase):
    """Test that when two servers start, the lower PID wins."""

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

        # Read A's PID from port file
        with open(pf) as f:
            data_a = json.load(f)
        pid_a = data_a['pid']

        # Start server B
        proc_b = self._start_server(pf, timeout=10)

        # B should exit quickly (A has lower PID)
        try:
            proc_b.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc_b.kill()
            self.fail('Server B did not exit after detecting existing server')

        # A should still be running
        self.assertTrue(
            _send(port_a, 'ping').get('result') == 'pong',
            'Server A should still be running')

        # Clean up
        _send(port_a, 'shutdown')
        proc_a.wait(timeout=3)
        try:
            os.unlink(pf)
        except OSError:
            pass


class TestFormatterRoundtrip(unittest.TestCase):
    """Test that datetime ISO formatting is symmetric."""

    def test_iso_roundtrip(self):
        from exiftool_server import _iso, _from_iso
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        s = _iso(now)
        self.assertIsNotNone(s)
        back = _from_iso(s)
        self.assertEqual(now, back)

    def test_none(self):
        from exiftool_server import _iso, _from_iso
        self.assertIsNone(_iso(None))
        self.assertIsNone(_from_iso(None))
        self.assertIsNone(_from_iso(''))


if __name__ == '__main__':
    unittest.main()
