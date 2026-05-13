#!/usr/bin/env python3
import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

import translate
import media
import btime


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


def resolve_target(current, delta, actual_dt):
    if current.year > 2030:
        return current - delta
    if current.year < 2020:
        return current + delta
    return current


def resolve_orig(files, delta, actual_dt, reprocess=False):
    map = {}
    for f in files:
        current = media.read_embedded(f, use_qt_utc=not reprocess)
        source = 'embedded'
        if current is None:
            if f.suffix.lower() == '.thm':
                for c in (f.with_suffix('.MP4'), f.with_suffix('.mp4'),
                          f.with_suffix('.LRV'), f.with_suffix('.lrv')):
                    if c.exists() and c in map:
                        current = map[c][0]
                        source = f'matched {c.name}'
                        break
        if current is None:
            current = media.read_mtime(f)
            source = 'mtime'
        if reprocess:
            target = current
        else:
            target = resolve_target(current, delta, actual_dt)
        map[f] = (current, source, target)
    return map


def main():
    parser = argparse.ArgumentParser(description='Correct GoPro media timestamps')
    parser.add_argument('directory', nargs='?', default='.', help='Target directory')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done')
    parser.add_argument('--fix-btime', nargs='?', const='auto',
                        choices=['auto', 'debugfs', 'fuse', 'clock'],
                        help='Fix creation time: auto (best for FS), debugfs, fuse, clock')
    parser.add_argument('--translation', help='Path to time translation file')
    parser.add_argument('--force', action='store_true', help='Re-process all files ignoring manifest')
    parser.add_argument('--reprocess', action='store_true',
                        help='Rewrite all files with UTC timezone handling (for already-corrected files)')
    args = parser.parse_args()

    target = Path(args.directory).resolve()
    if not target.is_dir():
        print(f"Error: {args.directory} is not a directory")
        sys.exit(1)

    if not media.exiftool_available():
        print("Error: exiftool not found")
        sys.exit(1)

    clean_exiftool_temp(target)

    tf = find_translation(target, args.translation)
    actual_dt, gopro_dt = translate.parse(tf)
    delta = actual_dt - gopro_dt

    print(f"Actual: {actual_dt}")
    print(f"GoPro:  {gopro_dt}")
    print(f"Delta:  {delta.days}d {(delta.seconds // 3600)}h {(delta.seconds % 3600) // 60}m")
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
    skipped_manifest = 0
    skipped_correct = 0

    for f in files:
        current, source, target_dt = resolved[f]

        if f.name in manifest:
            skipped_manifest += 1
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
    if skipped_manifest:
        summary.append(f"{skipped_manifest} already in manifest")
    if skipped_correct:
        summary.append(f"{skipped_correct} already correct")
    print(f"{'DRY RUN - ' if args.dry_run else ''}{', '.join(summary) if summary else 'No changes needed.'}")


if __name__ == '__main__':
    main()
