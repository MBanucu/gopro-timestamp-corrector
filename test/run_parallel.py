#!/usr/bin/env python3
"""Run test files in parallel — one subprocess per module.

Usage:
    PYTHONPATH=src python3 test/run_parallel.py
    PYTHONPATH=src python3 test/run_parallel.py -j 4
    PYTHONPATH=src python3 test/run_parallel.py --coverage
    PYTHONPATH=src python3 test/run_parallel.py test_analysis test_gps
"""
import argparse
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


HERE = Path(__file__).resolve().parent
SRC = HERE.parent / 'src'


def discover_all():
    import unittest
    loader = unittest.TestLoader()
    suite = loader.discover('test', pattern='test_*.py')
    modules = set()

    def walk(s):
        if isinstance(s, unittest.TestCase):
            mod = type(s).__module__
            if mod.startswith('test_'):
                modules.add(mod)
        elif hasattr(s, '__iter__'):
            for item in s:
                walk(item)

    walk(suite)
    return sorted(modules)


def run_one(name: str, verbose: bool, coverage_source: str | None) -> tuple[str, bool, float, str, str]:
    t0 = time.perf_counter()
    env = os.environ.copy()
    env['PYTHONPATH'] = f'{SRC}{os.pathsep}{env.get("PYTHONPATH", "")}'
    if coverage_source:
        cmd = [sys.executable, '-m', 'coverage', 'run',
               '--parallel-mode', '--source', coverage_source,
               '-m', 'unittest', f'test.{name}']
    else:
        cmd = [sys.executable, '-m', 'unittest',
               '-v' if verbose else '-q', f'test.{name}']
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    elapsed = time.perf_counter() - t0
    return name, r.returncode == 0, elapsed, r.stdout, r.stderr


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-j', '--jobs', type=int, default=os.cpu_count() or 4)
    parser.add_argument('-v', '--verbose', action='store_true')
    parser.add_argument('--coverage', action='store_true',
                        help='Collect coverage data from subprocesses')
    parser.add_argument('modules', nargs='*')
    args = parser.parse_args()

    modules = args.modules or discover_all()

    print(f'Running {len(modules)} modules, {args.jobs} workers\n')

    passed = failed = 0
    t_start = time.perf_counter()
    coverage_source = str(SRC) if args.coverage else None

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futs = {pool.submit(run_one, name, args.verbose, coverage_source): name
                for name in modules}
        for fut in as_completed(futs):
            name, ok, elapsed, out, err = fut.result()
            if ok:
                passed += 1
                print(f'  OK  {name}  ({elapsed:.1f}s)')
            else:
                failed += 1
                print(f'  FAIL {name}  ({elapsed:.1f}s)')
                for line in err.splitlines():
                    if 'FAIL' in line or 'Error' in line or 'AssertionError' in line:
                        print(f'    {line}')
                        break

    total = time.perf_counter() - t_start
    print(f'\n{passed} passed, {failed} failed ({total:.1f}s)')

    if args.coverage:
        subprocess.run([sys.executable, '-m', 'coverage', 'combine', '--quiet'])
        if passed > 0:
            subprocess.run([sys.executable, '-m', 'coverage', 'report', '-m',
                           f'--include={coverage_source}/*'])

    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
