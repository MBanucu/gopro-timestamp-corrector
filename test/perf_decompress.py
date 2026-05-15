#!/usr/bin/env python3
"""Benchmark the sparse image decompression + mount pipeline.

Usage:
    python3 test/perf_decompress.py              # status check only
    python3 test/perf_decompress.py --perf       # single benchmark run
    python3 test/perf_decompress.py --perf --runs 3  # average of 3
"""
import argparse
import gzip
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


CHUNK = 1024 * 1024  # 1 MiB
KNOWN_SIZE = 8531738624  # apparent (uncompressed) size of the sparse image


def _write_sparse(gz_path: Path, img_path: Path):
    fd = os.open(img_path, os.O_CREAT | os.O_WRONLY)
    os.ftruncate(fd, KNOWN_SIZE)
    os.close(fd)

    zero = b'\x00' * CHUNK
    offset = 0

    with gzip.open(gz_path, 'rb') as src, open(img_path, 'rb+') as dst:
        while True:
            chunk = src.read(CHUNK)
            if not chunk:
                break
            if chunk != zero[:len(chunk)]:
                os.lseek(dst.fileno(), offset, os.SEEK_SET)
                dst.write(chunk)
            offset += len(chunk)


def fmt_dur(sec: float) -> str:
    if sec < 1:
        return f"{sec * 1000:.0f} ms"
    if sec < 60:
        return f"{sec:.2f} s"
    return f"{sec // 60:.0f}m {sec % 60:.0f}s"


def _fmt(n: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def human_size(path: Path) -> tuple[str, str]:
    stat = path.stat()
    apparent = stat.st_size
    if hasattr(stat, 'st_blocks') and stat.st_blocks:
        actual = stat.st_blocks * 512
    else:
        actual = apparent
    return _fmt(apparent), _fmt(actual)


def run_once(gz_path: Path, run_label: str) -> dict:
    results = {}
    print(f"\n{'='*60}")
    print(f"  Run {run_label}")
    print(f"{'='*60}")

    apparent, actual = human_size(gz_path)
    print(f"\n  {gz_path.name}")
    print(f"    Apparent size:  {apparent}")
    print(f"    Actual on disk: {actual}")

    temp_dir = Path(tempfile.mkdtemp(prefix='gopro_perf_'))
    img_path = temp_dir / 'sdcard.img'

    t0 = time.perf_counter()
    _write_sparse(gz_path, img_path)
    t1 = time.perf_counter()
    results['decompress'] = t1 - t0
    apparent_i, actual_i = human_size(img_path)
    print(f"\n  Decompressed → {img_path.name}")
    print(f"    Apparent size:  {apparent_i}")
    print(f"    Actual on disk: {actual_i}")
    print(f"    Duration:       {fmt_dur(results['decompress'])}")

    t0 = time.perf_counter()
    res = subprocess.run(
        ['udisksctl', 'loop-setup', '-f', str(img_path), '--no-user-interaction'],
        capture_output=True, text=True)
    t1 = time.perf_counter()
    results['loop_setup'] = t1 - t0

    if res.returncode != 0:
        print(f"  ✗ udisksctl loop-setup failed: {res.stderr.strip()}")
        shutil.rmtree(temp_dir, ignore_errors=True)
        return results

    m = re.search(r'as (/dev/loop\d+)', res.stdout)
    loop_dev = m.group(1) if m else None
    if not loop_dev:
        print("  ✗ Could not parse loop device")
        shutil.rmtree(temp_dir, ignore_errors=True)
        return results

    print(f"\n  Loop device:    {loop_dev}")
    print(f"    Duration:       {fmt_dur(results['loop_setup'])}")

    t0 = time.perf_counter()
    res = subprocess.run(
        ['udisksctl', 'mount', '-b', loop_dev, '--no-user-interaction'],
        capture_output=True, text=True)
    t1 = time.perf_counter()
    results['mount'] = t1 - t0

    mount_point = None
    if res.returncode != 0:
        if 'AlreadyMounted' in res.stderr:
            m = re.search(r"at `([^`]+)'", res.stderr)
            if m:
                mount_point = m.group(1)
        if not mount_point:
            print(f"  ✗ mount failed: {res.stderr.strip()}")
    else:
        m = re.search(r'at ([^ \n]+)', res.stdout)
        if m:
            mount_point = m.group(1).rstrip('.')

    if mount_point:
        print(f"  Mount point:    {mount_point}")
    print(f"    Duration:       {fmt_dur(results['mount'])}")

    if mount_point:
        t0 = time.perf_counter()
        target = Path(mount_point) / 'DCIM' / '100GOPRO'
        files = sorted(target.glob('*')) if target.exists() else []
        t1 = time.perf_counter()
        results['verify'] = t1 - t0
        print(f"\n  Files at {target.name}: {len(files)}")
        print(f"    Duration:       {fmt_dur(results['verify'])}")
        for f in files:
            print(f"      {f.name}")

    if mount_point:
        t0 = time.perf_counter()
        subprocess.run(
            ['udisksctl', 'unmount', '-b', loop_dev, '--no-user-interaction'],
            capture_output=True)
        t1 = time.perf_counter()
        results['unmount'] = t1 - t0
        print(f"\n  Unmount:         {fmt_dur(results['unmount'])}")

    if loop_dev:
        t0 = time.perf_counter()
        subprocess.run(
            ['udisksctl', 'loop-delete', '-b', loop_dev, '--no-user-interaction'],
            capture_output=True)
        t1 = time.perf_counter()
        results['loop_delete'] = t1 - t0
        print(f"  Loop delete:     {fmt_dur(results['loop_delete'])}")

    t0 = time.perf_counter()
    shutil.rmtree(temp_dir, ignore_errors=True)
    t1 = time.perf_counter()
    results['cleanup'] = t1 - t0
    print(f"  Temp cleanup:    {fmt_dur(results['cleanup'])}")

    results['total'] = sum(results.values())
    print(f"\n  ── Total: {fmt_dur(results['total'])} ──")

    return results


def summary(all_results: list[dict]):
    if len(all_results) < 2:
        return
    keys = [k for k in all_results[0] if k != 'total']
    print(f"\n{'='*60}")
    print(f"  Summary over {len(all_results)} runs")
    print(f"{'='*60}")
    header = f"  {'Step':<20} {'Avg':>10} {'Min':>10} {'Max':>10}"
    print(header)
    print(f"  {'─'*len(header)}")
    for key in keys:
        vals = [r[key] for r in all_results if key in r]
        if vals:
            avg = sum(vals) / len(vals)
            mn = min(vals)
            mx = max(vals)
            print(f"  {key:<20} {fmt_dur(avg):>10} {fmt_dur(mn):>10} {fmt_dur(mx):>10}")
    totals = [r['total'] for r in all_results if 'total' in r]
    if totals:
        avg = sum(totals) / len(totals)
        mn = min(totals)
        mx = max(totals)
        print(f"  {'─'*len(header)}")
        print(f"  {'total':<20} {fmt_dur(avg):>10} {fmt_dur(mn):>10} {fmt_dur(mx):>10}")
    print()


def status(gz_path, img_path):
    print(f"{'='*60}")
    print(f"  Sparse image status")
    print(f"{'='*60}")
    for p, label in [(gz_path, 'Compressed'), (img_path, 'Decompressed')]:
        if p.exists():
            a, d = human_size(p)
            print(f"\n  {label}: {p.name}")
            print(f"    Apparent:       {a}")
            print(f"    Actual on disk: {d}")
        else:
            print(f"\n  {label}: {p.name} — not found")
    print(f"\n  Use --perf to run the full benchmark (fresh decompress + mount).")


def main():
    parser = argparse.ArgumentParser(
        description='Benchmark the sparse image decompression + mount pipeline')
    parser.add_argument('--perf', action='store_true',
                        help='Run the full benchmark (skipped by default)')
    parser.add_argument('--runs', type=int, default=1,
                        help='Number of runs to average (default: 1)')
    args = parser.parse_args()

    gz_path = Path(__file__).parent / 'sdcard.img.gz'
    if not gz_path.exists():
        print(f"Error: {gz_path} not found")
        sys.exit(1)

    img_path = Path(__file__).parent / 'sdcard.img'

    if not args.perf:
        status(gz_path, img_path)
        return

    all_results = []
    for i in range(args.runs):
        label = f"{i + 1}/{args.runs}" if args.runs > 1 else ""
        r = run_once(gz_path, label)
        all_results.append(r)

    summary(all_results)


if __name__ == '__main__':
    main()
