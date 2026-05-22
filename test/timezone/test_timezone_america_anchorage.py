"""Timezone test: America/Anchorage."""
import re
import unittest
from test.timezone.test_timezone_integration import run_tz


class TestAmericaAnchorage(unittest.TestCase):
    def test_pipeline(self):
        result = run_tz('America/Anchorage')
        out = result.stdout + result.stderr
        if 'Ran ' not in out:
            self.fail(
                "No tests discovered under TZ=America/Anchorage\n"
                "--- stdout ---\n" + result.stdout + "\n"
                "--- stderr ---\n" + result.stderr + "\n"
            )
        if result.returncode != 0:
            m = re.search(r'Ran (\\d+) test', out)
            n = int(m.group(1)) if m else 0
            self.fail(
                "Pipeline FAILED under TZ=America/Anchorage (" + str(n) + " tests)\n"
                "--- stdout ---\n" + result.stdout + "\n"
                "--- stderr ---\n" + result.stderr + "\n"
            )
        m = re.search(r'Ran (\\d+) test', out)
        n = int(m.group(1)) if m else 0
        if n == 0 or 'skipped' in out:
            self.skipTest("integration was skipped")
