"""Test that multiple concurrent ExifTool instances work independently.

Multiple ``ExifToolSession(connect=None)`` each spawn their own ``exiftool``
subprocess — completely independent, no shared global state.
This test verifies they can coexist and serve requests concurrently.
"""

import threading
import unittest

from exiftool_session import ExifToolSession


class TestMultipleInstances(unittest.TestCase):
    """Verify multiple independent exiftool processes can run concurrently."""

    def _make_sessions(self, n: int) -> list[ExifToolSession]:
        sessions = []
        for _ in range(n):
            s = ExifToolSession(connect=None)
            s.__enter__()
            sessions.append(s)
        return sessions

    def _close_sessions(self, sessions: list[ExifToolSession]):
        for s in sessions:
            try:
                s.__exit__(None, None, None)
            except Exception:
                pass

    def test_independent_sessions(self):
        """Multiple sessions in direct mode each spawn their own exiftool."""
        N = 5
        sessions = self._make_sessions(N)
        try:
            for i, s in enumerate(sessions):
                self.assertTrue(
                    s.available(),
                    f'Session {i} not available')
        finally:
            self._close_sessions(sessions)

    def test_concurrent_available(self):
        """Threads each create their own session and call available()."""
        N = 5
        results: list[bool | None] = [None] * N
        errors: list[Exception | None] = [None] * N

        def worker(idx: int):
            try:
                with ExifToolSession(connect=None) as s:
                    results[idx] = s.available()
            except Exception as e:
                errors[idx] = e

        threads = [
            threading.Thread(target=worker, args=(i,))
            for i in range(N)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        for i in range(N):
            self.assertIsNone(
                errors[i],
                f'Worker {i} raised: {errors[i]}')
            self.assertIsNotNone(
                results[i],
                f'Worker {i} produced no result')
            self.assertTrue(
                results[i],
                f'Worker {i} session not available')
