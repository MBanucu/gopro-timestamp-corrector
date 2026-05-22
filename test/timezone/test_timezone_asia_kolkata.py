"""Timezone test: Asia/Kolkata."""
import re
import unittest
from test.timezone.test_timezone_integration import run_tz


class TestAsiaKolkata(unittest.TestCase):
    def test_pipeline(self):
        result = run_tz('Asia/Kolkata')
        out = result.stdout + result.stderr
        if 'Ran ' not in out:
            self.fail(
                "No tests discovered under TZ=Asia/Kolkata\n"
                "--- stdout ---\n" + result.stdout + "\n"
                "--- stderr ---\n" + result.stderr + "\n"
            )
        if result.returncode != 0:
            m = re.search(r'Ran (\\d+) test', out)
            n = int(m.group(1)) if m else 0
            self.fail(
                "Pipeline FAILED under TZ=Asia/Kolkata (" + str(n) + " tests)\n"
                "--- stdout ---\n" + result.stdout + "\n"
                "--- stderr ---\n" + result.stderr + "\n"
            )
        m = re.search(r'Ran (\\d+) test', out)
        n = int(m.group(1)) if m else 0
        if n == 0 or 'skipped' in out:
            print("  [Asia/Kolkata] " + str(n) + " tests (integration was skipped)")
        else:
            print("  [Asia/Kolkata] " + str(n) + " tests passed")
