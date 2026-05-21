#!/usr/bin/env python3
"""
Orchestrator: reads files, calls calculator, passes result to writer.
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import analysis as an_mod
import preview
import resolve
import btime
import history
from exiftool_session import ExifToolSession
from options import BTIME_CLI_CHOICES, STRATEGY_MANUAL, STRATEGY_GPS
from writer import Writer, WriteJob


MANIFEST_NAME = '.timestamp_correction_log'


def clean_exiftool_temp(target):
    for p in target.glob('*_exiftool_tmp*'):
        p.unlink(missing_ok=True)
    for p in target.glob('*_original'):
        p.unlink(missing_ok=True)


def load_manifest(target):
    p = target / MANIFEST_NAME
    if p.exists():
        return set(line.strip() for line in p.read_text().splitlines() if line.strip())
    return set()


def save_manifest(target, entry):
    p = target / MANIFEST_NAME
    with open(p, 'a') as f:
        f.write(entry + '\n')


def _build_decisions_from_manifest(analysis_result, strategy_manifest, global_delta):
    """Build per-set strategy decisions, using global_delta as default for 'manual' sets."""
    overrides = strategy_manifest.get('sets', {})
    decisions = {}
    for fs in analysis_result.sets:
        override = overrides.get(fs.id, {})
        strategy = override.get('strategy', STRATEGY_MANUAL)
        if strategy == STRATEGY_GPS and not fs.has_any_gps:
            strategy = STRATEGY_MANUAL
        decisions[fs.id] = preview.SetDecision(
            strategy=strategy,
            manual_delta=global_delta if strategy == STRATEGY_MANUAL else None)
    return decisions


def main():
    parser = argparse.ArgumentParser(description='Correct GoPro media timestamps')
    parser.add_argument('directory', nargs='?', default='.', help='Target directory')
    parser.add_argument('--check', action='store_true', help='Check system environment and exit')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done')
    parser.add_argument('--fix-btime', nargs='?', const='auto',
                        choices=BTIME_CLI_CHOICES,
                        help='Fix creation time: auto (best for FS), debugfs, exfat_raw, fuse, clock')
    parser.add_argument('--gps', action='store_true', help='Use GPS time from the first file to determine delta')
    parser.add_argument('--timezone', help='Timezone for GPS correction (e.g. Europe/Berlin)')
    parser.add_argument('--force', action='store_true', help='Re-process all files ignoring manifest')
    parser.add_argument('--strategy-manifest', help='JSON file with per-set strategy decisions')
    args = parser.parse_args()

    if args.check:
        import env_check
        report = env_check.check_env(args.directory)
        print(env_check.format_summary(report))
        sys.exit(0 if report.exiftool.available else 1)

    target = Path(args.directory).resolve()
    if not target.is_dir():
        print(f"Error: {args.directory} is not a directory")
        sys.exit(1)

    clean_exiftool_temp(target)

    with ExifToolSession() as session:
        if not session.available():
            print("Error: exiftool not found")
            sys.exit(1)

        # ── 1. Read all files via shared analysis (single exiftool batch) ──
        strategy_manifest_raw = None
        if args.strategy_manifest:
            strategy_manifest_raw = json.loads(Path(args.strategy_manifest).read_text())

        analysis_result = an_mod.analyze(session, target)
        if not analysis_result.total_files:
            print("No media files found.")
            return

        # ── 2. Compute global delta ─────────────────────────────────
        needs_global_delta = args.gps or not args.strategy_manifest
        if args.strategy_manifest:
            sets_m = strategy_manifest_raw.get('sets', {})
            if not needs_global_delta:
                needs_global_delta = any(s.get('strategy', STRATEGY_MANUAL) == STRATEGY_MANUAL for s in sets_m.values())

        if needs_global_delta:
            if args.gps:
                gps_file = None
                gps_utc = None
                gopro_dt = None
                for fs in analysis_result.sets:
                    for fi in fs.files:
                        if fi.gps_time:
                            gps_file = fi.path
                            gps_utc = fi.gps_time
                            gopro_dt = fi.embedded_time
                            break
                    if gps_file:
                        break
                if not gps_file:
                    print("Error: No file with GPS data found.")
                    sys.exit(1)

                print(f"Using GPS from {gps_file.name}")

                if not gps_utc:
                    print(f"Error: Could not read GPS from {gps_file.name}")
                    sys.exit(1)

                if args.timezone:
                    import zoneinfo
                    try:
                        tz = zoneinfo.ZoneInfo(args.timezone)
                    except Exception as e:
                        print(f"Error: Invalid timezone: {args.timezone} ({e})")
                        sys.exit(1)
                    gps_utc_tz = gps_utc.replace(tzinfo=timezone.utc)
                    actual_dt = gps_utc_tz.astimezone(tz).replace(tzinfo=None)
                else:
                    actual_dt = gps_utc

                if not gopro_dt:
                    print(f"Error: Could not read embedded time from {gps_file.name}")
                    sys.exit(1)

                global_delta = resolve.gps_delta(actual_dt, gopro_dt)
            else:
                print("Error: No GPS data available and no translation file provided.")
                sys.exit(1)

            print(f"Actual: {actual_dt}")
            print(f"GoPro:  {gopro_dt}")
            print(f"Delta:  {global_delta.days}d {(global_delta.seconds // 3600)}h {(global_delta.seconds % 3600) // 60}m")
        else:
            global_delta = timedelta()
            actual_dt = datetime.now()
            gopro_dt = actual_dt

        print()

        # ── 3. Build decisions + compute plan (calculator) ─────────
        if args.strategy_manifest:
            decisions = _build_decisions_from_manifest(analysis_result, strategy_manifest_raw, global_delta)
        else:
            decisions = {fs.id: preview.SetDecision(strategy=STRATEGY_MANUAL, manual_delta=global_delta)
                         for fs in analysis_result.sets}

        plan = preview.compute_preview(analysis_result, decisions, global_delta)

        # ── 4. Display plan ────────────────────────────────────────
        manifest = set() if args.force else load_manifest(target)
        if manifest:
            print(f"Manifest: {len(manifest)} files previously processed")
        print()

        if args.dry_run:
            print("DRY RUN\n")

        processed = 0
        would_process = 0
        skipped_manifest = 0
        skipped_correct = 0

        pending_jobs: list[WriteJob] = []

        for pr in plan:
            for fp in pr.file_results:
                in_manifest = fp.path.name in manifest

                current_dt = fp.current_embedded or fp.current_mtime
                target_dt = fp.target_embedded or fp.target_mtime

                if in_manifest:
                    skipped_manifest += 1
                    continue

                if target_dt is not None and current_dt is not None and target_dt == current_dt:
                    skipped_correct += 1
                    continue

                print(f"  {fp.path.name}")
                source_str = f'{pr.strategy} ({fp.source})' if pr.strategy != fp.source else fp.source
                print(f"    {current_dt}  ({source_str})  \u2192  {target_dt}")
                would_process += 1

                if not args.dry_run:
                    pending_jobs.append(WriteJob(
                        path=fp.path,
                        target_embedded=fp.target_embedded,
                        target_mtime=fp.target_mtime,
                    ))

        # ── 5. Write plan via writer ────────────────────────────────
        if pending_jobs and not args.dry_run:
            history_meta = {
                'global_delta': str(global_delta) if global_delta else None,
                'fix_btime': args.fix_btime or 'off',
                'sets': {
                    pr.set_id: {
                        'strategy': pr.strategy,
                        'delta': str(pr.applied_delta) if pr.applied_delta else None,
                    }
                    for pr in plan
                },
            }
            run_dir = history.begin_run(target, history_meta)
            history.capture_before(session, run_dir, [j.path for j in pending_jobs])

            with Writer(target, fix_btime=args.fix_btime, delta=global_delta,
                        dry_run=False, session=session) as w:
                summary = w.write_all(pending_jobs)

            history.capture_after(session, run_dir, [j.path for j in pending_jobs])
            history.finalize_run(run_dir, summary.written, summary.skipped, summary.errors)
            for job in pending_jobs:
                save_manifest(target, job.path.name)
            processed = len(pending_jobs)

    print()
    summary_parts = []
    if args.dry_run:
        if would_process:
            summary_parts.append(f"{would_process} would be processed")
    elif processed:
        summary_parts.append(f"{processed} corrected")
    if skipped_manifest:
        summary_parts.append(f"{skipped_manifest} already in manifest")
    if would_process == 0 and skipped_correct and not skipped_manifest:
        summary_str = 'No changes needed.'
    else:
        if skipped_correct:
            summary_parts.append(f"{skipped_correct} already correct")
        summary_str = ', '.join(summary_parts)
    print(f"{'DRY RUN - ' if args.dry_run else ''}{summary_str}")


if __name__ == '__main__':
    main()
