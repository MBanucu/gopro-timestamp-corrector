"""Birth-time correction — facade dispatching to strategy classes.

Public API is unchanged: all existing callers continue to work.
Internally each btime method is now a :class:`BtimeStrategy` subclass
registered in :data:`strategies.REGISTRY`.
"""

import os
import subprocess
from datetime import timedelta
from pathlib import Path

from options import BTIME_AUTO
from strategies import REGISTRY


# ── Registry helpers ────────────────────────────────────────────

def _strategy(method: str):
    """Return the :class:`BtimeStrategy` instance for *method*, or ``None``."""
    cls = REGISTRY.get(method)
    if cls is None:
        return None
    return cls()


# ── Public API (unchanged signatures) ───────────────────────────

def resolve_method(method: str, fs_type: str | None) -> str | None:
    # Explicit method names pass through directly
    if method in REGISTRY:
        return method
    # 'auto' resolves to the best concrete method for the filesystem
    if fs_type in ('ext2', 'ext3', 'ext4'):
        return 'debugfs'
    if fs_type == 'exfat':
        return 'exfat_raw'
    if fs_type in ('vfat', 'msdos'):
        return 'fuse'
    return 'clock'


def compatible_methods(fs_type: str | None) -> tuple[str, ...]:
    methods = []
    if fs_type:
        for name, cls in REGISTRY.items():
            if fs_type in cls.compatible_filesystems():
                methods.append(name)
    methods.append('clock')
    return tuple(methods)


def needs_processing_before(method: str) -> bool:
    return method == 'fuse'


def needs_processing_after(method: str) -> bool:
    return method in ('debugfs', 'exfat_raw')


def setup(method: str, target_path, delta: timedelta, dry_run: bool) -> dict | None:
    s = _strategy(method)
    if s is None:
        return {}
    if s.needs_setup():
        return s.setup(target_path, delta, dry_run) or {}
    return {}


def teardown(method: str, ctx: dict, dry_run: bool):
    s = _strategy(method)
    if s is not None:
        s.teardown(ctx, dry_run)


def fix_file(method: str, filepath, dt, ctx: dict, dry_run: bool):
    s = _strategy(method)
    if s is not None:
        s.fix_file(filepath, dt, ctx, dry_run)


def chain_setup(methods, target_path, fs_type, delta, dry_run):
    expanded = []
    for m in methods:
        if m == BTIME_AUTO:
            expanded.extend(
                cm for cm in compatible_methods(fs_type) if cm != BTIME_AUTO
            )
        else:
            expanded.append(m)

    for method in expanded:
        resolved = resolve_method(method, fs_type)
        if resolved is None:
            continue
        s = _strategy(resolved)
        if s is None:
            continue

        if s.needs_setup():
            ctx = s.setup(target_path, delta, dry_run) or {}
            if not ctx and resolved == 'fuse':
                continue
            return resolved, ctx

        if resolved == 'clock':
            ctx = s.setup(target_path, delta, dry_run) or {}
            return resolved, ctx

        if resolved == 'exfat_raw':
            if os.path.exists(target_path) and _resolve_device(target_path) is None:
                continue

        return resolved, {}

    return None, {}


# ── Filesystem / device helpers (shared by strategies) ──────────

def detect_fs(path):
    try:
        result = subprocess.run(
            ['df', '--output=fstype', str(path)],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            lines = result.stdout.strip().splitlines()
            if len(lines) >= 2:
                fs = lines[1].strip()
                if fs:
                    return 'exfat' if fs == 'fuseblk' else fs
    except (FileNotFoundError, OSError):
        pass
    return _detect_fs_from_mounts(path)


def _detect_fs_from_mounts(path):
    try:
        with open('/proc/mounts') as f:
            mounts = [(ln.split()[0], ln.split()[1], ln.split()[2])
                      for ln in f if len(ln.split()) >= 3]
    except OSError:
        return None
    path_str = str(path)
    best = (None, 0)
    for dev, mp, fs in mounts:
        if path_str.startswith(mp) and len(mp) > best[1]:
            best = ('exfat' if fs == 'fuseblk' else fs, len(mp))
    return best[0]


def _resolve_device(path):
    st = os.stat(path)
    major = os.major(st.st_dev)
    minor = os.minor(st.st_dev)
    with open('/proc/partitions') as f:
        for line in f:
            parts = line.split()
            if len(parts) == 4 and parts[0].isdigit():
                if int(parts[0]) == major and int(parts[1]) == minor:
                    return f'/dev/{parts[3]}'
    try:
        link = os.readlink(f'/sys/dev/block/{major}:{minor}')
        return os.path.join('/dev', os.path.basename(link))
    except OSError:
        return None


def _resolve_mount_point(path):
    r = subprocess.run(
        ['findmnt', '-n', '-o', 'TARGET', '--target', str(path)],
        capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    return None


# ── Backward-compatible aliases for private functions ───────────

from strategies.exfat_raw import _fix_exfat_raw  # noqa: E402, F401
