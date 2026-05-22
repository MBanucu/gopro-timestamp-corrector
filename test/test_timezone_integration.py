"""Shared helpers for per‑timezone integration tests.

Each timezone gets its own ``test_timezone_<name>.py`` module so that
``run_parallel.py`` can run them all in parallel (one subprocess per TZ).

When adding a new timezone:
1. Add it to ``TIMEZONES`` below.
2. Run ``python3 test/test_timezone_integration.py`` to regenerate wrappers.
"""
import os
import subprocess
import sys
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

TEST_MODULE = 'test.test_full_auto_integration.TestFullAutoIntegration.test_full_pipeline'


def slug(tz: str) -> str:
    return tz.lower().replace('/', '_').replace('-', '_')


def cls_name(tz: str) -> str:
    return ''.join(w.capitalize() for w in slug(tz).split('_'))


def run_tz(tz: str) -> subprocess.CompletedProcess:
    """Run the full pipeline inside a subprocess with ``TZ={tz}``."""
    test_dir = Path(__file__).parent.resolve()
    repo_root = test_dir.parent
    env = os.environ.copy()
    env['TZ'] = tz
    env['PYTHONPATH'] = f'src:{test_dir}'
    return subprocess.run(
        [sys.executable, '-m', 'unittest', TEST_MODULE, '-v'],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )


def _regenerate_wrappers():
    """Create/overwrite ``test_timezone_<slug>.py`` for every TZ in TIMEZONES."""
    here = Path(__file__).parent.resolve()
    for tz in TIMEZONES:
        s = slug(tz)
        cn = cls_name(tz)
        content = '''"""Timezone test: {tz}."""
import re
import unittest
from test_timezone_integration import run_tz


class Test{cn}(unittest.TestCase):
    def test_pipeline(self):
        result = run_tz('{tz}')
        out = result.stdout + result.stderr
        if 'Ran ' not in out:
            self.fail(
                "No tests discovered under TZ={tz}\\n"
                "--- stdout ---\\n" + result.stdout + "\\n"
                "--- stderr ---\\n" + result.stderr + "\\n"
            )
        if result.returncode != 0:
            m = re.search(r'Ran (\\\\d+) test', out)
            n = int(m.group(1)) if m else 0
            self.fail(
                "Pipeline FAILED under TZ={tz} (" + str(n) + " tests)\\n"
                "--- stdout ---\\n" + result.stdout + "\\n"
                "--- stderr ---\\n" + result.stderr + "\\n"
            )
        m = re.search(r'Ran (\\\\d+) test', out)
        n = int(m.group(1)) if m else 0
        if n == 0 or 'skipped' in out:
            print("  [{tz}] " + str(n) + " tests (integration was skipped)")
        else:
            print("  [{tz}] " + str(n) + " tests passed")
'''.format(tz=tz, cn=cn)
        (here / f'test_timezone_{s}.py').write_text(content.lstrip('\n'))
    print(f'Regenerated {len(TIMEZONES)} wrapper files.')


if __name__ == '__main__':
    _regenerate_wrappers()
