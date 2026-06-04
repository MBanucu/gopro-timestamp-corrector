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


def _tools_on_system() -> frozenset[str]:
    """Return a frozenset of every external tool available on ``$PATH``.

    Used by :func:`viable_methods` to filter strategies whose dependencies
    are fully present.
    """
    import shutil
    seen: set[str] = set()
    for cls in REGISTRY.values():
        for tool in cls.required_tools():
            if tool not in seen and shutil.which(tool) is not None:
                seen.add(tool)
    return frozenset(seen)


def _sudo_works() -> bool:
    """Return True if passwordless ``sudo`` works."""
    try:
        r = subprocess.run(
            ['sudo', '-n', 'true'],
            capture_output=True, timeout=10,
        )
        return r.returncode == 0
    except Exception:
        return False


# ── Capability-based filtering ──────────────────────────────────

def viable_methods(
    fs_type: str | None,
    *,
    tools_available: frozenset[str] | None = None,
    sudo_available: bool | None = None,
) -> tuple[str, ...]:
    """Like :func:`compatible_methods` but skips strategies whose tool
    dependencies are not met on the current system.

    Capability filtering is **only** applied when *both* ``tools_available``
    and ``sudo_available`` are passed explicitly.  When omitted the
    function falls back to pure filesystem compatibility
    (i.e. identical to :func:`compatible_methods` for callers that do
    not have an environment report).

    This avoids surprising behaviour in contexts (e.g. tests, sandboxed
    builds) where ``sudo`` or optional tools like ``faketime`` are not
    present but the caller wants to know what *would* be available on
    a properly set‑up machine.
    """
    if tools_available is None or sudo_available is None:
        return compatible_methods(fs_type)

    methods: list[str] = []
    if fs_type:
        for name, cls in REGISTRY.items():
            if cls.is_internal():
                continue
            if fs_type in cls.compatible_filesystems() \
               and cls.check_capabilities(tools_available, sudo_available):
                methods.append(name)
    return tuple(methods)


# ── Public API (unchanged signatures) ───────────────────────────

def resolve_method(method: str, fs_type: str | None) -> str | None:
    if method == BTIME_AUTO:
        if fs_type in ('ext2', 'ext3', 'ext4'):
            return 'debugfs'
        if fs_type == 'exfat':
            return 'exfat_raw'
        return None
    if method in REGISTRY:
        return method
    return None


def compatible_methods(fs_type: str | None) -> tuple[str, ...]:
    methods = []
    if fs_type:
        for name, cls in REGISTRY.items():
            if cls.is_internal():
                continue
            if fs_type in cls.compatible_filesystems():
                methods.append(name)
    return tuple(methods)


def needs_processing_before(method: str) -> bool:
    return False


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
    viable = set(viable_methods(fs_type)) if fs_type else set()
    expanded = []
    for m in methods:
        if m == BTIME_AUTO:
            expanded.extend(
                cm for cm in compatible_methods(fs_type)
                if cm != BTIME_AUTO and cm in viable
            )
        elif m not in viable:
            print(f"  ! Skipping '{m}': not viable on {fs_type or 'unknown'} filesystem")
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
            return resolved, ctx

        if resolved == 'exfat_raw':
            if os.path.exists(target_path) and _resolve_device(target_path) is None:
                continue

        return resolved, {}

    return None, {}


# ── Filesystem / device helpers — delegated to exfat-raw package ─

from exfat_raw._resolve import detect_fs, resolve_device as _resolve_device, resolve_mount_point as _resolve_mount_point  # noqa: F401, E402

