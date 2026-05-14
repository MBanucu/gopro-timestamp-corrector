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
import translate
import media
import btime
from writer import Writer, WriteJob


MANIFEST_NAME = '.timestamp_correction_log'


def clean_exiftool_temp(target):
    for p in target.glob('*_exiftool_tmp*'):
        p.unlink(missing_ok=True)
    for p in target.glob('*_original'):
        p.unlink(missing_ok=True)


def find_translation(target_path, cli_path):
    if cli_path:
        p = Path(cli_path)
        if not p.exists():
            print(f"Error: Translation file not found: {cli_path}")
            sys.exit(1)
        return p
    translations = list(target_path.glob('*time translation*'))
    if not translations:
        print("Error: No time translation file found. Use --translation to specify one.")
        sys.exit(1)
    return translations[0]


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
        strategy = override.get('strategy', 'manual')
        if strategy == 'gps' and not fs.has_any_gps:
            strategy = 'manual'
        decisions[fs.id] = preview.SetDecision(
            strategy=strategy,
            manual_delta=global_delta if strategy == 'manual' else None)
    return decisions


def main():
    parser = argparse.ArgumentParser(description='Correct GoPro media timestamps')
    parser.add_argument('directory', nargs='?', default='.', help='Target directory')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done')
    parser.add_argument('--fix-btime', nargs='?', const='auto',
                        choices=['auto', 'debugfs', 'fuse', 'clock'],
                        help='Fix creation time: auto (best for FS), debugfs, fuse, clock')
    parser.add_argument('--translation', help='Path to time translation file')
    parser.add_argument('--gps', action='store_true', help='Use GPS time from the first file to determine delta')
    parser.add_argument('--timezone', help='Timezone for GPS correction (e.g. Europe/Berlin)')
    parser.add_argument('--force', action='store_true', help='Re-process all files ignoring manifest')
    parser.add_argument('--reprocess', action='store_true',
                        help='Re-write all files with delta=0 (ignores manifest)')
    parser.add_argument('--strategy-manifest', help='JSON file with per-set strategy decisions')
    args = parser.parse_args()

    target = Path(args.directory).resolve()
    if not target.is_dir():
        print(f"Error: {args.directory} is not a directory")
        sys.exit(1)

    if not media.exiftool_available():
        print("Error: exiftool not found")
        sys.exit(1)

    clean_exiftool_temp(target)

    # ── 1. Compute global delta ─────────────────────────────────
    needs_global_delta = args.gps or not args.strategy_manifest
    strategy_manifest_raw = None
    if args.strategy_manifest:
        strategy_manifest_raw = json.loads(Path(args.strategy_manifest).read_text())
        sets = strategy_manifest_raw.get('sets', {})
        if not needs_global_delta:
            needs_global_delta = any(s.get('strategy', 'manual') == 'manual' for s in sets.values())

    if needs_global_delta:
        if args.gps:
            files = media.collect(target)
            gps_file = None
            gps_utc = None
            for f in files:
                gps_utc = media.read_gps_time(f)
                if gps_utc:
                    gps_file = f
                    break
            if not gps_file:
                print("Error: No file with GPS data found.")
                sys.exit(1)

            print(f"Using GPS from {gps_file.name}")

            gps_utc = media.read_gps_time(gps_file)
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

            gopro_dt = media.read_embedded(gps_file, use_qt_utc=False)
            if not gopro_dt:
                print(f"Error: Could not read embedded time from {gps_file.name}")
                sys.exit(1)

            global_delta = resolve.gps_delta(actual_dt, gopro_dt)
        else:
            tf = find_translation(target, args.translation)
            actual_dt, gopro_dt = translate.parse(tf)
            global_delta = actual_dt - gopro_dt

        print(f"Actual: {actual_dt}")
        print(f"GoPro:  {gopro_dt}")
        print(f"Delta:  {global_delta.days}d {(global_delta.seconds // 3600)}h {(global_delta.seconds % 3600) // 60}m")
    else:
        global_delta = timedelta()
        actual_dt = datetime.now()
        gopro_dt = actual_dt

    print()

    # ── 2. Read all files via shared analysis ──────────────────
    analysis_result = an_mod.analyze(target)
    if not analysis_result.total_files:
        print("No media files found.")
        return

    if args.reprocess:
        print("REPROCESS mode: re-writing all files with no delta applied")
        print()

    # ── 3. Build decisions + compute plan (calculator) ─────────
    if args.reprocess:
        # In reprocess mode, target = current → use delta=0 for all
        decisions = {fs.id: preview.SetDecision(strategy='manual', manual_delta=timedelta())
                     for fs in analysis_result.sets}
    elif args.strategy_manifest:
        decisions = _build_decisions_from_manifest(analysis_result, strategy_manifest_raw, global_delta)
    else:
        # Default: all sets use the global delta
        decisions = {fs.id: preview.SetDecision(strategy='manual', manual_delta=global_delta)
                     for fs in analysis_result.sets}

    # The plan is computed ONCE and reused for display + writing
    plan = preview.compute_preview(analysis_result, decisions, global_delta)

    # ── 4. Display plan (same for dry-run and normal) ──────────
    manifest = set() if (args.force or args.reprocess) else load_manifest(target)
    if manifest:
        print(f"Manifest: {len(manifest)} files previously processed")
    print()

    if args.dry_run:
        print("DRY RUN\n")

    processed = 0
    would_process = 0
    skipped_manifest = 0
    skipped_correct = 0

    # Collect plan items that need writing
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

    # ── 5. Write plan via writer (shared module, no recalculation) ──
    if pending_jobs and not args.dry_run:
        with Writer(target, fix_btime=args.fix_btime, delta=global_delta, dry_run=False) as w:
            summary = w.write_all(pending_jobs)
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
