#!/usr/bin/env python3
"""Run test files in parallel — one subprocess per module.

Tests are NOT thread-safe: always use ``-j 1`` when running against
real (or loop) mounted filesystems to avoid cross-mount I/O races
(see ``AGENTS.md`` kernel exFAT bug).  Higher ``-j`` values are safe
for pure-unit tests that don't touch block devices.

Usage:
    PYTHONPATH=src python3 test/run_parallel.py
    PYTHONPATH=src python3 test/run_parallel.py -j 1
    PYTHONPATH=src python3 test/run_parallel.py --coverage
    PYTHONPATH=src python3 test/run_parallel.py test_analysis test_gps
    PYTHONPATH=src python3 test/run_parallel.py hypothesis
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
            last = mod.rsplit('.', 1)[-1]
            if last.startswith('test_'):
                # Ensure all discovered modules have the 'test.' prefix
                if not mod.startswith('test.'):
                    mod = 'test.' + mod
                modules.add(mod)
        elif hasattr(s, '__iter__'):
            for item in s:
                walk(item)

    walk(suite)
    return sorted(modules)


def expand_modules(names: list[str]) -> list[str]:
    """Expand short names into full dotted module paths.

    - ``test.xxx`` → kept as-is
    - ``test_xxx`` → ``test.test_xxx``
    - ``xxx`` (no ``test_`` prefix) → look for ``test/xxx/`` directory
      and return all ``test.xxx.test_*`` submodules found there
    """
    result = []
    for name in names:
        if name.startswith('test.'):
            result.append(name)
        elif name.startswith('test_'):
            result.append('test.' + name)
        else:
            subdir = HERE / name
            if subdir.is_dir():
                import unittest
                loader = unittest.TestLoader()
                suite = loader.discover(str(subdir), pattern='test_*.py',
                                        top_level_dir=str(HERE))
                found = set()
                def walk(s):
                    if isinstance(s, unittest.TestCase):
                        mod = type(s).__module__
                        # discover returns modules relative to top_level_dir,
                        # so hypothesis.test_h9_to_h12 → test.hypothesis.test_h9_to_h12
                        if mod.startswith(name + '.'):
                            found.add('test.' + mod)
                    elif hasattr(s, '__iter__'):
                        for item in s:
                            walk(item)
                walk(suite)
                if found:
                    result.extend(sorted(found))
                    continue
            result.append('test.' + name)
    return result


def run_one(name: str, coverage_source: str | None) -> tuple[str, str, float, str, str]:
    """Run a test module, return (name, status, elapsed, stdout, stderr).

    Status is ``'ok'``, ``'fail'``, or ``'skip'``.
    """
    t0 = time.perf_counter()
    env = os.environ.copy()
    env['PYTHONPATH'] = f'{SRC}{os.pathsep}{env.get("PYTHONPATH", "")}'
    module = name
    if coverage_source:
        cmd = [sys.executable, '-m', 'coverage', 'run',
               '--parallel-mode', '--source', coverage_source,
               '-m', 'unittest', module, '-v']
    else:
        cmd = [sys.executable, '-m', 'unittest', module, '-v']
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    elapsed = time.perf_counter() - t0
    # unittest writes to stderr, not stdout
    body = r.stderr or ''
    last = body.strip().split('\n')[-1] if body else ''
    if r.returncode != 0:
        status = 'fail'
    elif '(skipped=' in last:
        status = 'skip'
    else:
        status = 'ok'
    # Diagnostic: report streams + detected status
    out_len = len(r.stdout) if r.stdout else 0
    err_len = len(r.stderr) if r.stderr else 0
    diag = (f'[run_parallel] {name}: rc={r.returncode} status={status} '
            f'out_len={out_len} err_len={err_len} '
            f'last={last!r}\n')
    sys.stderr.write(diag)
    sys.stderr.flush()
    return name, status, elapsed, r.stdout, r.stderr


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-j', '--jobs', type=int, default=os.cpu_count() or 4)
    parser.add_argument('-v', '--verbose', action='store_true',  # kept for compat, always verbose now
                       help=argparse.SUPPRESS)
    parser.add_argument('--coverage', action='store_true',
                        help='Collect coverage data from subprocesses')
    parser.add_argument('--ignore', '-x', action='append', default=[],
                        help='Exclude a test module (may be repeated)')
    parser.add_argument('modules', nargs='*')
    args = parser.parse_args()

    modules = expand_modules(args.modules) if args.modules else discover_all()
    for ign in args.ignore:
        modules = [m for m in modules if m != ign]

    print(f'Running {len(modules)} modules, {args.jobs} workers\n')

    passed = failed = skipped = 0
    t_start = time.perf_counter()
    coverage_source = str(SRC) if args.coverage else None

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futs = {pool.submit(run_one, name, coverage_source): name
                for name in modules}
        for fut in as_completed(futs):
            name, status, elapsed, out, err = fut.result()
            if status == 'ok':
                passed += 1
                print(f'  OK  {name}  ({elapsed:.1f}s)')
            elif status == 'skip':
                skipped += 1
                print(f'  SKIP {name}  ({elapsed:.1f}s)')
            else:
                failed += 1
                print(f'  FAIL {name}  ({elapsed:.1f}s)')
                # Print full failure output (unittest writes to stderr)
                for line in err.splitlines():
                    print(f'    {line}')

    total = time.perf_counter() - t_start
    parts = [f'{passed} passed']
    if skipped:
        parts.append(f'{skipped} skipped')
    if failed:
        parts.append(f'{failed} failed')
    print(f'\n{", ".join(parts)} ({total:.1f}s)')

    total_with_skips = passed + skipped
    if args.coverage and total_with_skips > 0:
        subprocess.run([sys.executable, '-m', 'coverage', 'combine', '--quiet'])
        subprocess.run([sys.executable, '-m', 'coverage', 'report', '-m',
                       f'--include={coverage_source}/*'])

    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
