#!/usr/bin/env python3
import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import translate
import media
import btime


MANIFEST_NAME = '.timestamp_correction_log'


def _group_key(path):
    m = re.search(r'(\d{6,})', path.stem)
    return m.group(1) if m else None


def _group_by_stem(files):
    groups = {}
    for f in files:
        key = _group_key(f)
        if key:
            groups.setdefault(key, []).append(f)
    return groups


def _resolve_current(f, resolved_map, reprocess):
    current = media.read_embedded(f, use_qt_utc=not reprocess)
    source = 'embedded'
    if current is None:
        if f.suffix.lower() == '.thm':
            for c in (f.with_suffix('.MP4'), f.with_suffix('.mp4'),
                      f.with_suffix('.LRV'), f.with_suffix('.lrv')):
                if c.exists() and c in resolved_map:
                    current = resolved_map[c][0]
                    source = f'matched {c.name}'
                    break
    if current is None:
        current = media.read_mtime(f)
        source = 'mtime'
    return current, source


def _find_gps_in_group(files):
    for f in files:
        gps = media.read_gps_time(f)
        if gps:
            return gps, f
    return None, None


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


def resolve_target(current, delta, actual_dt):
    return current + delta


def resolve_orig(files, delta, actual_dt, reprocess=False):
    map = {}
    for f in files:
        current, source = _resolve_current(f, map, reprocess)
        if reprocess:
            target = current
        else:
            target = resolve_target(current, delta, actual_dt)
        map[f] = (current, source, target)
    return map


def resolve_with_strategies(files, delta, actual_dt, manifest, reprocess=False):
    strategies = manifest.get('sets', {})
    groups = _group_by_stem(files)
    result = {}

    for stem_id, group_files in sorted(groups.items()):
        strat_info = strategies.get(stem_id, {'strategy': 'manual'})
        strategy = strat_info.get('strategy', 'manual')

        if strategy == 'skip':
            for f in group_files:
                current, source = _resolve_current(f, result, reprocess)
                result[f] = (current, f'skip ({source})', current)
            continue

        if strategy == 'gps':
            gps_time, gps_file = _find_gps_in_group(group_files)
            if gps_time is not None:
                gps_utc_tz = gps_time.replace(tzinfo=timezone.utc)
                local_tz = datetime.now().astimezone().tzinfo
                actual_dt_local = gps_utc_tz.astimezone(local_tz).replace(tzinfo=None)
                first_emb = None
                for f in group_files:
                    emb = media.read_embedded(f, use_qt_utc=not reprocess)
                    if emb:
                        first_emb = emb
                        break
                if first_emb is not None:
                    set_delta = actual_dt_local - first_emb
                else:
                    set_delta = delta
                source_tag = 'gps'
            else:
                set_delta = delta
                source_tag = 'gps_fallback'
        else:
            set_delta = delta
            source_tag = 'manual'

        for f in group_files:
            current, source = _resolve_current(f, result, reprocess)
            if reprocess:
                target = current
            else:
                target = resolve_target(current, set_delta, actual_dt)
            result[f] = (current, f'{source_tag} ({source})', target)

    return result


def resolve_gps_delta(gps_file, reprocess, timezone_arg=None):
    gps_utc = media.read_gps_time(gps_file)
    if not gps_utc:
        return None, None, None

    if timezone_arg:
        import zoneinfo
        try:
            tz = zoneinfo.ZoneInfo(timezone_arg)
        except Exception as e:
            print(f"Error: Invalid timezone: {timezone_arg} ({e})")
            sys.exit(1)
        gps_utc_tz = gps_utc.replace(tzinfo=timezone.utc)
        actual_dt = gps_utc_tz.astimezone(tz).replace(tzinfo=None)
    else:
        local_tz = datetime.now().astimezone().tzinfo
        gps_utc_tz = gps_utc.replace(tzinfo=timezone.utc)
        actual_dt = gps_utc_tz.astimezone(local_tz).replace(tzinfo=None)

    gopro_dt = media.read_embedded(gps_file, use_qt_utc=not reprocess)
    if not gopro_dt:
        return None, None, None

    delta = actual_dt - gopro_dt
    return delta, actual_dt, gopro_dt


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
                        help='Rewrite all files with UTC timezone handling (for already-corrected files)')
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

    # Parse strategy manifest early to know if we need a global delta
    strategy_manifest = None
    needs_global_delta = args.gps or not args.strategy_manifest
    if args.strategy_manifest:
        strategy_manifest = json.loads(Path(args.strategy_manifest).read_text())
        sets = strategy_manifest.get('sets', {})
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

            delta, actual_dt, gopro_dt = resolve_gps_delta(gps_file, args.reprocess, args.timezone)
            if delta is None:
                print(f"Error: Could not read GPS or embedded time from {gps_file.name}")
                sys.exit(1)
        else:
            tf = find_translation(target, args.translation)
            actual_dt, gopro_dt = translate.parse(tf)
            delta = actual_dt - gopro_dt

        print(f"Actual: {actual_dt}")
        print(f"GoPro:  {gopro_dt}")
        print(f"Delta:  {delta.days}d {(delta.seconds // 3600)}h {(delta.seconds % 3600) // 60}m")
    else:
        delta = timedelta()
        actual_dt = datetime.now()
        gopro_dt = actual_dt

    print()

    manifest = set() if (args.force or args.reprocess) else load_manifest(target)
    if manifest:
        print(f"Manifest: {len(manifest)} files previously processed")
    print()

    files = media.collect(target)
    if not files:
        print("No media files found.")
        return

    if args.reprocess:
        print("REPROCESS mode: rewriting all files with UTC timezone handling")
        print()

    if args.strategy_manifest:
        sm = json.loads(Path(args.strategy_manifest).read_text())
        resolved = resolve_with_strategies(files, delta, actual_dt, sm, reprocess=args.reprocess)
    else:
        resolved = resolve_orig(files, delta, actual_dt, reprocess=args.reprocess)

    if args.dry_run:
        print("DRY RUN\n")

    b_ctx = {}
    b_method = None
    if args.fix_btime:
        fs = btime.detect_fs(target)
        b_method = btime.resolve_method(args.fix_btime, fs)
        print(f"FS: {fs}  |  btime: {b_method}\n")

        if btime.needs_processing_before(b_method):
            b_ctx = btime.setup(b_method, target, delta, args.dry_run) or {}
            if not b_ctx and b_method == 'fuse':
                b_method = 'clock'
                b_ctx = btime.setup(b_method, target, delta, args.dry_run) or {}

        if b_method == 'clock':
            b_ctx = btime.setup(b_method, target, delta, args.dry_run) or {}
            first_dt = list(resolved.values())[1] if resolved else None
            if isinstance(first_dt, tuple) and len(first_dt) == 3:
                btime.fix_file(b_method, None, first_dt[2], b_ctx, args.dry_run)

    processed = 0
    would_process = 0
    skipped_manifest_count = 0
    skipped_correct = 0

    for f in files:
        if f not in resolved:
            continue
        current, source, target_dt = resolved[f]

        if f.name in manifest:
            skipped_manifest_count += 1
            continue

        if not args.reprocess and target_dt == current:
            skipped_correct += 1
            continue

        print(f"  {f.name}")
        print(f"    {current}  ({source})  \u2192  {target_dt}")
        would_process += 1

        if args.dry_run:
            continue

        if media.write_embedded(f, target_dt):
            print(f"    \u2713  Embedded metadata")
        else:
            print(f"    \u2717  Embedded metadata skipped")

        media.write_mtime(f, target_dt)
        print(f"    \u2713  mtime")
        processed += 1

        save_manifest(target, f.name)

        if btime.needs_processing_after(b_method):
            btime.fix_file(b_method, f, target_dt, b_ctx, args.dry_run)

    if b_method and (btime.needs_processing_before(b_method) or b_method == 'clock'):
        btime.teardown(b_method, b_ctx, args.dry_run)

    print()
    summary = []
    if args.dry_run:
        if would_process:
            summary.append(f"{would_process} would be processed")
    elif processed:
        summary.append(f"{processed} corrected")
    if skipped_manifest_count:
        summary.append(f"{skipped_manifest_count} already in manifest")
    if would_process == 0 and skipped_correct and not skipped_manifest_count:
        summary_str = 'No changes needed.'
    else:
        if skipped_correct:
            summary.append(f"{skipped_correct} already correct")
        summary_str = ', '.join(summary)
    print(f"{'DRY RUN - ' if args.dry_run else ''}{summary_str}")


if __name__ == '__main__':
    main()
