#!/usr/bin/env python3
"""Run test files in parallel using subprocesses + thread pool.

Usage:
    PYTHONPATH=src python3 test/run_parallel.py
    PYTHONPATH=src python3 test/run_parallel.py -j 4
    PYTHONPATH=src python3 test/run_parallel.py test.test_analysis test.test_gps
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

# Tests that mount the sdcard image — run serially to avoid loop-device contention
_IMAGE_TESTS = frozenset({
    'test_img', 'test_strategy', 'test_btime',
    'test_auto_calibrate_integration', 'test_full_auto_integration',
})


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


def run_one(name: str, verbose: bool) -> tuple[str, bool, float, str]:
    t0 = time.perf_counter()
    env = os.environ.copy()
    env['PYTHONPATH'] = f'{SRC}{os.pathsep}{env.get("PYTHONPATH", "")}'
    cmd = [sys.executable, '-m', 'unittest', '-v' if verbose else '-q',
           f'test.{name}']
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    elapsed = time.perf_counter() - t0
    ok = r.returncode == 0
    return name, ok, elapsed, r.stdout, r.stderr


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-j', '--jobs', type=int, default=os.cpu_count() or 4)
    parser.add_argument('-v', '--verbose', action='store_true')
    parser.add_argument('modules', nargs='*')
    args = parser.parse_args()

    modules = args.modules or discover_all()
    image_mods = [m for m in modules if m in _IMAGE_TESTS]
    other_mods = [m for m in modules if m not in _IMAGE_TESTS]

    print(f'Running {len(modules)} test modules ({args.jobs} workers)')
    print(f'  Image tests (serial): {len(image_mods)}')
    print(f'  Other tests (parallel): {len(other_mods)}\n')

    passed = failed = 0
    t_start = time.perf_counter()

    def run_and_report(name):
        nonlocal passed, failed
        _, ok, elapsed, out, err = run_one(name, args.verbose)
        if ok:
            passed += 1
            print(f'  OK  {name}  ({elapsed:.1f}s)')
        else:
            failed += 1
            print(f'  FAIL {name}  ({elapsed:.1f}s)')
            # Show first failure line
            for line in err.splitlines():
                if 'FAIL' in line or 'Error' in line or 'AssertionError' in line:
                    print(f'    {line}')
                    break
        return ok

    # Serial: image tests
    if image_mods:
        print('── Image tests (serial) ──')
        for name in image_mods:
            run_and_report(name)

    # Parallel: other tests
    if other_mods:
        print(f'── Other tests ({args.jobs} workers) ──')
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futs = {pool.submit(run_one, name, args.verbose): name
                    for name in other_mods}
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
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
