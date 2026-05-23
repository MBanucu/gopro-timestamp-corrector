"""Tests for ExifTool server, client, and session integration.

These tests cover:
- Server ping / status / shutdown protocol
- Client auto-spawn and connection
- ``ExifToolSession(connect='auto')`` delegation
- Idle timeout auto-shutdown
- Protocol error handling

(pid-election tests moved to ``test_pid_election.py``.)
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


PORT_FILE = os.path.join(
    tempfile.gettempdir(), 'gopro-exiftool-server.json')
IDLE_TIMEOUT = 3  # short timeout for tests


def _clean_port_file(path: str | None = None):
    if path is None:
        path = PORT_FILE
    try:
        os.unlink(path)
    except OSError:
        pass


# Isolated port files per test class so no class interferes with another's server.
_PROTOCOL_PORT_FILE = PORT_FILE + '.protocol_test'
_CLIENT_PORT_FILE = PORT_FILE + '.client_test'
_SESSION_PORT_FILE = PORT_FILE + '.session_test'
_IDLE_PORT_FILE = PORT_FILE + '.idle_test'


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
    """Test the raw TCP protocol against a server subprocess.

    Uses an isolated port file (``_PROTOCOL_PORT_FILE``) so its server
    doesn't interfere with other test classes or the production default.
    """

    @classmethod
    def setUpClass(cls):
        _clean_port_file(_PROTOCOL_PORT_FILE)
        cls._proc = subprocess.Popen(
            [sys.executable, os.path.join(_BD, 'exiftool_server.py'),
             '--idle-timeout', str(IDLE_TIMEOUT),
             '--port-file', _PROTOCOL_PORT_FILE],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Wait for port file
        deadline = time.monotonic() + 5
        cls._port = None
        while time.monotonic() < deadline:
            try:
                with open(_PROTOCOL_PORT_FILE) as f:
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
        _clean_port_file(_PROTOCOL_PORT_FILE)

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
        pf = _PROTOCOL_PORT_FILE + '.shutdown_test'
        _clean_port_file(pf)
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
    """Test the client auto-spawn feature.

    Uses an isolated port file (``_CLIENT_PORT_FILE``) so auto-spawned
    servers don't leak into other test classes.
    """

    def setUp(self):
        _clean_port_file(_CLIENT_PORT_FILE)

    def tearDown(self):
        try:
            with open(_CLIENT_PORT_FILE) as f:
                data = json.load(f)
            _send(data['port'], 'shutdown')
        except (OSError, json.JSONDecodeError, KeyError, ConnectionError):
            pass
        _clean_port_file(_CLIENT_PORT_FILE)

    def test_auto_spawn(self):
        from exiftool_client import ExifToolClient
        # Patch ExifToolClient to use our isolated port file
        import exiftool_client as ec
        orig_port = ec._PORT_FILE
        ec._PORT_FILE = _CLIENT_PORT_FILE
        try:
            client = ExifToolClient()
            self.assertTrue(client.available())
        finally:
            ec._PORT_FILE = orig_port

    def test_ping_after_connect(self):
        from exiftool_client import ExifToolClient
        import exiftool_client as ec
        orig_port = ec._PORT_FILE
        ec._PORT_FILE = _CLIENT_PORT_FILE
        try:
            client = ExifToolClient()
            resp = _send(client._port, 'ping')
            self.assertEqual(resp.get('result'), 'pong')
        finally:
            ec._PORT_FILE = orig_port

    def test_read_gps_nonexistent_file(self):
        from exiftool_client import ExifToolClient
        import exiftool_client as ec
        orig_port = ec._PORT_FILE
        ec._PORT_FILE = _CLIENT_PORT_FILE
        try:
            client = ExifToolClient()
            result = client.read_gps_time('/nonexistent/file.mp4')
            self.assertIsNone(result)
        finally:
            ec._PORT_FILE = orig_port


class TestSessionIntegration(unittest.TestCase):
    """Test ExifToolSession(connect='auto') delegation.

    Uses an isolated port file (``_SESSION_PORT_FILE``) so auto-spawned
    servers don't leak into other test classes.
    """

    def setUp(self):
        _clean_port_file(_SESSION_PORT_FILE)

    def tearDown(self):
        try:
            with open(_SESSION_PORT_FILE) as f:
                data = json.load(f)
            _send(data['port'], 'shutdown')
        except (OSError, json.JSONDecodeError, KeyError, ConnectionError):
            pass
        _clean_port_file(_SESSION_PORT_FILE)

    def test_session_available(self):
        from exiftool_session import ExifToolSession
        import exiftool_client as ec
        orig = ec._PORT_FILE
        ec._PORT_FILE = _SESSION_PORT_FILE
        try:
            with ExifToolSession(connect='auto') as session:
                self.assertTrue(session.available())
        finally:
            ec._PORT_FILE = orig

    def test_session_reuses_server(self):
        """Two sessions should share the same server process."""
        from exiftool_session import ExifToolSession
        import exiftool_client as ec
        orig = ec._PORT_FILE
        ec._PORT_FILE = _SESSION_PORT_FILE
        try:
            with ExifToolSession(connect='auto') as s1:
                self.assertTrue(s1.available())
                with ExifToolSession(connect='auto') as s2:
                    self.assertTrue(s2.available())
        finally:
            ec._PORT_FILE = orig


class TestIdleShutdown(unittest.TestCase):
    """Test that the server shuts down after idle timeout."""

    def test_idle_timeout(self):
        _clean_port_file(_IDLE_PORT_FILE)
        proc = subprocess.Popen(
            [sys.executable, os.path.join(_BD, 'exiftool_server.py'),
             '--idle-timeout', '2', '--port-file', _IDLE_PORT_FILE],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        deadline = time.monotonic() + 5
        port = None
        while time.monotonic() < deadline:
            try:
                with open(_IDLE_PORT_FILE) as f:
                    data = json.load(f)
                port = data['port']
                break
            except (OSError, json.JSONDecodeError, KeyError):
                time.sleep(0.05)
        self.assertIsNotNone(port)

        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            self.fail('Server did not auto-shutdown within idle timeout')

        _clean_port_file(_IDLE_PORT_FILE)

class TestFormatterRoundtrip(unittest.TestCase):
    """Test that datetime ISO formatting is symmetric."""

    def test_iso_roundtrip(self):
        from exiftool_protocol import iso, from_iso
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        s = iso(now)
        self.assertIsNotNone(s)
        back = from_iso(s)
        self.assertEqual(now, back)

    def test_none(self):
        from exiftool_protocol import iso, from_iso
        self.assertIsNone(iso(None))
        self.assertIsNone(from_iso(None))
        self.assertIsNone(from_iso(''))


if __name__ == '__main__':
    unittest.main()
