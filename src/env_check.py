"""Check system environment and report available capabilities.

Reports which btime methods and correction strategies are usable
on the current system.  Called by CLI (``--check``) and GUI
(environment dialog).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import btime
import options
from probe import (
    BtimeProbeResult,
    ExfatProbeResult,
    probe_btime,
    probe_exfat_btime,
)
from strategies import REGISTRY


# ── Data types ──────────────────────────────────────────────────────

_TOOL_LABELS: dict[str, str] = {
    'debugfs': 'debugfs (e2fsprogs)',
    'dd': 'dd (coreutils)',
    'findmnt': 'findmnt (util-linux)',
    'faketime': 'faketime (libfaketime)',
    'mount.exfat-fuse': 'mount.exfat-fuse (exfat)',
    'timedatectl': 'timedatectl (systemd)',
    'date': 'date (coreutils)',
    'exiftool': 'exiftool',
    'sudo': 'sudo',
    'sync': 'sync',
    'mount': 'mount (util-linux)',
    'umount': 'umount (util-linux)',
}


@dataclass
class ToolAvailability:
    name: str
    label: str
    path: str | None
    available: bool


@dataclass
class BtimeMethodCapability:
    name: str
    label: str
    dependencies: list[ToolAvailability]
    all_deps_met: bool
    compatible_fs: list[str]
    requires_sudo: bool


@dataclass
class EnvReport:
    platform: str
    python_version: str
    sudo_available: bool
    tkinter_importable: bool
    tkinter_display: bool
    exiftool: ToolAvailability
    btime_methods: list[BtimeMethodCapability]
    available_strategies: list[str]
    btime_probe: BtimeProbeResult | None
    exfat_btime_support: ExfatProbeResult | None


# ── Helpers ─────────────────────────────────────────────────────────

def _which(name: str) -> str | None:
    p = shutil.which(name)
    return p if p else None


def _tool(name: str) -> ToolAvailability:
    return ToolAvailability(
        name=name,
        label=_TOOL_LABELS.get(name, name),
        path=_which(name),
        available=_which(name) is not None,
    )


def _check_sudo() -> bool:
    s = _which('sudo')
    if not s:
        return False
    try:
        r = subprocess.run(
            [s, '-n', 'true'],
            capture_output=True, timeout=10,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, PermissionError):
        return False


def _check_tk_importable() -> bool:
    try:
        import tkinter  # noqa: F401
        return True
    except Exception:
        return False


def _check_tk_display() -> bool:
    try:
        import tkinter
        r = tkinter.Tk()
        r.destroy()
        return True
    except Exception:
        return False


def _btime_method_deps(method: str) -> list[str]:
    cls = REGISTRY.get(method)
    if cls is None:
        return []
    return list(cls.required_tools())


# ── Btime method labels (from strategy classes) ─────────────────────

_BTIME_LABELS: dict[str, str] = {
    name: cls.label for name, cls in REGISTRY.items()
}


# ── Main check ──────────────────────────────────────────────────────

def check_env(target_path: str | Path | None = None) -> EnvReport:
    """Probe the system and return an :class:`EnvReport`."""
    sudo_ok = _check_sudo()

    exif = _tool('exiftool')
    if exif.available:
        try:
            r = subprocess.run(
                [exif.path, '-ver'],
                capture_output=True, text=True, timeout=15,
            )
            if r.returncode == 0 and r.stdout.strip():
                exif = ToolAvailability(
                    name='exiftool', label='exiftool',
                    path=exif.path, available=True,
                )
        except Exception:
            pass

    fs_type = None
    if target_path is not None:
        try:
            fs_type = btime.detect_fs(str(target_path))
        except Exception:
            pass

    btime_methods: list[BtimeMethodCapability] = []
    for method in (options.BTIME_EXFAT_RAW, options.BTIME_EXFAT_RAW_READ,
                   options.BTIME_DEBUGFS,
                   options.BTIME_FUSE, options.BTIME_CLOCK):
        deps = [_tool(d) for d in _btime_method_deps(method)]
        all_met = all(d.available for d in deps) and sudo_ok

        btime_methods.append(BtimeMethodCapability(
            name=method,
            label=_BTIME_LABELS.get(method, method),
            dependencies=deps,
            all_deps_met=all_met,
            compatible_fs=list(btime.compatible_methods(
                _fs_for_method(method)
            )),
            requires_sudo=True,
        ))

    probe = probe_btime(target_path or '.') if target_path else None

    exfat_probe = None
    if sudo_ok:
        exfat_probe = probe_exfat_btime()

    return EnvReport(
        platform=sys.platform,
        python_version=f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}',
        sudo_available=sudo_ok,
        tkinter_importable=_check_tk_importable(),
        tkinter_display=_check_tk_display(),
        exiftool=exif,
        btime_methods=btime_methods,
        available_strategies=[options.STRATEGY_GPS, options.STRATEGY_MANUAL, options.STRATEGY_SKIP],
        btime_probe=probe,
        exfat_btime_support=exfat_probe,
    )


def _fs_for_method(method: str) -> str:
    from strategies import REGISTRY
    cls = REGISTRY.get(method)
    if cls is None:
        return 'unknown'
    compat = cls.compatible_filesystems()
    return compat[0] if compat else 'unknown'


# ── Pretty printing ─────────────────────────────────────────────────

def format_summary(report: EnvReport) -> str:
    lines: list[str] = []
    lines.append(f'Platform:         {report.platform}')
    lines.append(f'Python:           {report.python_version}')
    tk_icon = '✓' if report.tkinter_importable else '✗'
    disp_icon = '✓' if report.tkinter_display else '✗'
    lines.append(f'Tkinter module:   {tk_icon}  display: {disp_icon}')
    lines.append(f'exiftool:         {report.exiftool.path or "✗ not found"}')
    lines.append(f'Sudo (no-pass):   {"✓" if report.sudo_available else "✗"}')

    lines.append('')
    lines.append('Strategies:')
    for s in report.available_strategies:
        lines.append(f'  ✓ {s}')

    lines.append('')
    lines.append('Btime methods:')
    for m in report.btime_methods:
        icon = '✓' if m.all_deps_met else '✗'
        compat = ', '.join(m.compatible_fs) if m.compatible_fs else 'none'
        lines.append(f'  {icon} {m.label:28s}  FS: {compat}')
        for dep in m.dependencies:
            dep_icon = '✓' if dep.available else '✗'
            lines.append(f'      {dep_icon} {dep.label}')
        if m.requires_sudo and not report.sudo_available:
            lines.append(f'      (needs passwordless sudo)')

    if report.btime_probe:
        p = report.btime_probe
        lines.append('')
        lines.append(f'Birth time probe ({p.path}):')
        lines.append(f'  Filesystem:      {p.fs_type or "unknown"}')
        lines.append(f'  stat -c %W:      {p.stat_btime if p.stat_btime is not None else "N/A"}')
        lines.append(f'  statx STATX_BTIME: {p.statx_btime if p.statx_btime is not None else "N/A"}')
        if p.statx_supported is True:
            lines.append(f'  statx btime:     ✓  (kernel reports birth time)')
        elif p.statx_supported is False:
            lines.append(f'  statx btime:     ✗  (kernel does NOT report birth time)')
        else:
            lines.append(f'  statx btime:     ?  (syscall failed)')
        if p.utime_works is True:
            lines.append(f'  os.utime():      ✓')
        elif p.utime_works is False:
            lines.append(f'  os.utime():      ✗  (EPERM or other error)')
        else:
            lines.append(f'  os.utime():      ?  (unexpected error)')

    if report.exfat_btime_support is not None:
        e = report.exfat_btime_support
        overall = '✓' if e.supported else ('✗' if e.supported is False else '?')
        lines.append('')
        lines.append(f'exFAT btime probe (temp filesystem):')
        for label, val in [
            ("stat -c '%W'",            e.stat_method),
            ('statx STATX_BTIME',       e.statx_method),
            ('raw block readback',      e.raw_read_method),
            ('os.utime()',              e.utime_on_exfat),
            ('dd iflag=nocache',        e.dd_nocache),
            ('blockdev --flushbufs',    e.blockdev_flush),
        ]:
            icon = '✓' if val is True else ('✗' if val is False else '?')
            lines.append(f'  {label:28s} {icon}')
        lines.append(f'  overall:                   {overall}')
        if e.reason:
            lines.append(f'  ({e.reason})')

    return '\n'.join(lines)


# ── CLI entry point ─────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Check system environment for GoPro Timestamp Corrector')
    parser.add_argument('directory', nargs='?', default=None,
                        help='Optional target directory for filesystem-specific checks')
    args = parser.parse_args()

    report = check_env(args.directory)
    print(format_summary(report))
    sys.exit(0 if report.exiftool.available else 1)


if __name__ == '__main__':
    main()
