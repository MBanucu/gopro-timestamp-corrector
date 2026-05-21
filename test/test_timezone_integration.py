"""Run the full integration pipeline under multiple system timezones.

Each timezone runs in an isolated subprocess to verify the timestamp
correction produces identical results regardless of local timezone.

Invoke under nix develop so the subprocess python has pyexiftool:

    nix develop --command python3 -m unittest test.test_timezone_integration -v
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path


TIMEZONES = [
    'UTC',
    'Asia/Tokyo',
    'Asia/Kolkata',
    'Europe/Berlin',
    'America/New_York',
    'America/Anchorage',
    'Pacific/Auckland',
]

_TIMEOUT = 600


class TestTimezoneIntegration(unittest.TestCase):
    """Run the full auto integration pipeline under each system TZ."""

    def test_full_pipeline_under_timezones(self):
        test_dir = Path(__file__).parent.resolve()
        repo_root = test_dir.parent
        module = 'test.test_full_auto_integration.TestFullAutoIntegration.test_full_pipeline'

        for tz in TIMEZONES:
            with self.subTest(tz=tz):
                env = os.environ.copy()
                env['TZ'] = tz
                env['PYTHONPATH'] = f'src:{test_dir}'
                r = subprocess.run(
                    [sys.executable, '-m', 'unittest', module, '-v'],
                    cwd=str(repo_root),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=_TIMEOUT,
                )
                if r.returncode != 0:
                    msg = f'Integration test FAILED under TZ={tz}\n'
                    if r.stdout:
                        msg += f'--- stdout ---\n{r.stdout}\n'
                    if r.stderr:
                        msg += f'--- stderr ---\n{r.stderr}\n'
                    self.fail(msg)


if __name__ == '__main__':
    unittest.main()
