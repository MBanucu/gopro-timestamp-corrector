#!/usr/bin/env python3
"""Supervisor: run test suite and count exiftool processes per test module.

Samples /proc every 200ms during each module's execution, capturing both
the peak concurrent count (while the test runs) and the final count
(after the module completes but before the next one starts).

Uses ``run_parallel.py``'s stderr diagnostic (``[run_parallel] …``) as the
module-completion signal — these are written immediately when a module's
subprocess finishes, before the coverage-report flood on stdout.

Usage:
    python3 exiftool_supervisor.py                        # run all, -j 1
    python3 exiftool_supervisor.py -- -j 4                # override parallelism
    python3 exiftool_supervisor.py test_exiftool_server   # specific module
"""

import os
import re
import select
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path


HERE = Path(__file__).resolve().parent

_DONE_RE = re.compile(
    r'^\[run_parallel\] (\S+): rc=\d+ status=(\S+)'
)

_EXIFTOOL_RE = re.compile(
    r'(?:exiftool_server\.py|exiftool\s+-stay_open)'
)


def _scan_exiftool() -> dict[int, str]:
    """Return {pid: cmdline} for processes running exiftool daemon."""
    result: dict[int, str] = {}
    try:
        for entry in os.listdir('/proc'):
            if not entry.isdigit():
                continue
            try:
                with open(f'/proc/{entry}/cmdline', 'rb') as f:
                    raw = f.read().replace(b'\x00', b' ').decode(
                        'utf-8', errors='replace'
                    )
                if _EXIFTOOL_RE.search(raw):
                    result[int(entry)] = raw.strip()
            except (OSError, IOError):
                continue
    except FileNotFoundError:
        pass
    return result


def main() -> int:
    extra_args = sys.argv[1:] if len(sys.argv) > 1 else []
    cmd = ['nix', 'run', '.#test', '--', '-j', '1', *extra_args]

    baseline = _scan_exiftool()
    print(f'[{datetime.now():%H:%M:%S}] Starting: {" ".join(cmd)}')
    print(f'[{datetime.now():%H:%M:%S}] Baseline exiftool processes: {len(baseline)}')
    for pid, cmdline in sorted(baseline.items()):
        print(f'      [{pid}] {cmdline}')
    print()

    # Background sampler: scans every 200ms, tracks peak new processes
    _peak_lock = threading.Lock()
    _peak_new = 0
    _sampler_stop = threading.Event()

    def _sampler():
        nonlocal _peak_new
        while not _sampler_stop.is_set():
            now = _scan_exiftool()
            new_count = sum(1 for pid in now if pid not in baseline)
            with _peak_lock:
                if new_count > _peak_new:
                    _peak_new = new_count
            _sampler_stop.wait(0.2)

    sampler = threading.Thread(target=_sampler, daemon=True)
    sampler.start()

    results: list[tuple[str, str, int, int, dict[int, str]]] = []

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(HERE),
    )

    # Forward both streams line-by-line; use stderr lines as completion signal
    assert proc.stdout is not None
    assert proc.stderr is not None

    # Read both streams: iterate stderr as the completion-signal source,
    # and periodically flush stdout (non-blocking via select).
    def _flush_stdout():
        """Forward available stdout lines without blocking."""
        while True:
            r, _, _ = select.select([proc.stdout], [], [], 0)
            if not r:
                break
            line = proc.stdout.readline()
            if not line:
                break
            sys.stdout.write(line)
            sys.stdout.flush()

    for err_line in iter(proc.stderr.readline, ''):
        sys.stdout.write(err_line)
        sys.stdout.flush()

        m = _DONE_RE.match(err_line)
        if not m:
            continue

        module, status = m.group(1), m.group(2)

        # Flush any buffered stdout before reporting
        _flush_stdout()

        # Read peak from sampler and reset
        with _peak_lock:
            peak = _peak_new
            _peak_new = 0

        # Final count at module boundary
        now = _scan_exiftool()
        final_procs = {pid: cmdline for pid, cmdline in now.items()
                       if pid not in baseline}
        final_n = len(final_procs)
        results.append((module, status, peak, final_n, final_procs))

        ts_str = datetime.now().strftime('%H:%M:%S')
        parts = [f'[{ts_str}] {module}: peak={peak}, final={final_n}']
        if final_n > 0:
            for pid, c in sorted(final_procs.items()):
                parts.append(f'      [{pid}] {c}')
        print('\n'.join(parts))

    # Forward remaining stdout
    _flush_stdout()
    proc.wait()

    _sampler_stop.set()
    sampler.join()

    # Summary table
    print()
    print('=' * 72)
    print('SUMMARY: exiftool processes per test module')
    print('=' * 72)
    print(f'  {"Module":40s} {"Status":6s} {"Peak":>6s} {"Final":>6s}')
    print(f'  {"-"*40} {"-"*6} {"-"*6} {"-"*6}')
    for module, status, peak, final_n, _procs in results:
        print(f'  {module:40s} {status:6s} {str(peak):>6s} {str(final_n):>6s}')
    print()

    global_peak = max((p for _, _, p, _, _ in results), default=0)
    print(f'  Global peak concurrent exiftool processes: {global_peak}')

    return 0 if proc.returncode == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
