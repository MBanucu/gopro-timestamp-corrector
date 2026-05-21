"""Check system environment and report available capabilities.

Reports which btime methods and correction strategies are usable
on the current system.  Called by CLI (``--check``) and GUI
(environment dialog).
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import btime
import options
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
class BtimeProbeResult:
    """Result of probing birth-time readability on a filesystem."""

    path: str
    fs_type: str | None
    stat_btime: int | None
    statx_btime: int | None
    statx_supported: bool | None
    utime_works: bool | None


@dataclass
class ExfatBtimeSupport:
    """Result of probing the kernel's ability to read back btime on exFAT.

    Creates a temporary exFAT filesystem, writes a file, and checks
    whether ``stat -c '%W'`` returns a non-zero birth time.
    """

    supported: bool | None
    reason: str = ''


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
    exfat_btime_support: ExfatBtimeSupport | None


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


# ── Btime probing (stat / statx) ────────────────────────────────────


def _has_statx() -> bool:
    """Return True if the ``statx()`` syscall is available."""
    try:
        libc = ctypes.CDLL(ctypes.util.find_library('c'), use_errno=True)
        libc.statx.restype = ctypes.c_int
        return True
    except Exception:
        return False


def _probe_stat_btime(path: str) -> int | None:
    """Return birth time (epoch seconds) via ``stat -c '%W'``, or None."""
    r = subprocess.run(
        ['stat', '-c', '%W', path],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode != 0:
        return None
    val = r.stdout.strip()
    try:
        return int(val)
    except ValueError:
        return None


def _probe_statx_btime(path: str) -> tuple[int | None, bool | None]:
    """Return ``(btime_epoch, stx_mask_has_btime)`` via ``statx()``, or ``(None, None)``.

    ``stx_mask_has_btime`` is ``True`` when the kernel explicitly reports
    birth time, ``False`` when it doesn't (even if ``btime`` happens to be 0).
    Returns ``(None, None)`` when the syscall itself fails.
    """
    try:
        class statx_timestamp(ctypes.Structure):
            _fields_ = [
                ('tv_sec', ctypes.c_int64),
                ('tv_nsec', ctypes.c_uint32),
                ('__reserved', ctypes.c_int32),
            ]

        class statx_buf(ctypes.Structure):
            _fields_ = [
                ('stx_mask', ctypes.c_uint32),
                ('stx_blksize', ctypes.c_uint32),
                ('stx_attributes', ctypes.c_uint64),
                ('stx_nlink', ctypes.c_uint32),
                ('stx_uid', ctypes.c_uint32),
                ('stx_gid', ctypes.c_uint32),
                ('stx_mode', ctypes.c_uint16),
                ('__spare0', ctypes.c_uint16 * 1),
                ('stx_ino', ctypes.c_uint64),
                ('stx_size', ctypes.c_uint64),
                ('stx_blocks', ctypes.c_uint64),
                ('stx_attributes_mask', ctypes.c_uint64),
                ('stx_atime', statx_timestamp),
                ('stx_btime', statx_timestamp),
                ('stx_ctime', statx_timestamp),
                ('stx_mtime', statx_timestamp),
                ('stx_rdev_major', ctypes.c_uint32),
                ('stx_rdev_minor', ctypes.c_uint32),
                ('stx_dev_major', ctypes.c_uint32),
                ('stx_dev_minor', ctypes.c_uint32),
                ('__spare2', ctypes.c_uint64 * 14),
            ]

        STATX_BTIME = 0x00000200
        libc = ctypes.CDLL(ctypes.util.find_library('c'), use_errno=True)
        buf = statx_buf()
        ret = libc.statx(-100, path.encode(), 0, STATX_BTIME, ctypes.byref(buf))
        if ret != 0:
            return None, None
        mask_ok = bool(buf.stx_mask & STATX_BTIME)
        return buf.stx_btime.tv_sec, mask_ok
    except Exception:
        return None, None


def _probe_utime(path: str) -> bool | None:
    """Check whether os.utime() works on this filesystem."""
    fname = None
    try:
        f = tempfile.NamedTemporaryFile(dir=path, delete=False)
        fname = f.name
        f.close()
        ts = 1234567890.0
        os.utime(fname, (ts, ts))
        st = os.stat(fname)
        return abs(st.st_mtime - ts) < 1.0
    except OSError:
        return False
    except Exception:
        return None
    finally:
        if fname is not None and os.path.exists(fname):
            os.unlink(fname)


def _probe_exfat_btime() -> ExfatBtimeSupport:
    """Probe exFAT btime readback via raw block access (works on all kernels).

    Creates a temp exFAT filesystem, writes a file, then reads the birth time
    directly from the on‑disk directory entry using :func:`read_exfat_btime_raw`.
    This bypasses the kernel's ``statx`` interface, so it works on kernels
    before 6.12 where the exFAT driver does not advertise ``STATX_BTIME``.
    """
    if not shutil.which('mkfs.exfat'):
        return ExfatBtimeSupport(supported=None, reason='mkfs.exfat not found')
    if not shutil.which('losetup'):
        return ExfatBtimeSupport(supported=None, reason='losetup not found')

    tmp_img = None
    loop_dev = None
    mount_point = None
    test_file = None
    try:
        fd, tmp_img = tempfile.mkstemp(suffix='.img', prefix='exfat_btime_probe_')
        os.close(fd)
        os.truncate(tmp_img, 64 * 1024 * 1024)  # 64 MB sparse

        r = subprocess.run(['mkfs.exfat', tmp_img], capture_output=True, timeout=30)
        if r.returncode != 0:
            return ExfatBtimeSupport(supported=None,
                                     reason=f'mkfs.exfat failed: {r.stderr.strip()}')

        r = subprocess.run(
            ['sudo', 'losetup', '-f', '--show', tmp_img],
            capture_output=True, text=True, timeout=15)
        if r.returncode != 0 or not r.stdout.strip():
            return ExfatBtimeSupport(supported=None, reason='losetup failed')
        loop_dev = r.stdout.strip()

        mount_point = tempfile.mkdtemp(prefix='exfat_btime_probe_')

        # Try kernel exfat driver first
        r = subprocess.run(
            ['sudo', 'mount', '-t', 'exfat', loop_dev, mount_point],
            capture_output=True, timeout=15)

        # Fall back to FUSE (mount.exfat-fuse) when kernel driver is
        # unavailable or sudo PATH doesn't include the Nix store.
        if r.returncode != 0:
            mount_exfat = shutil.which('mount.exfat-fuse')
            if mount_exfat:
                r = subprocess.run(
                    ['sudo', 'env', f'PATH={os.environ["PATH"]}',
                     mount_exfat, loop_dev, mount_point,
                     '-o', f'uid={os.getuid()}', '-o', f'gid={os.getgid()}',
                     '-o', 'allow_other'],
                    capture_output=True, timeout=15)
            if r.returncode != 0:
                msg = r.stderr.decode() if isinstance(r.stderr, bytes) else str(r.stderr)
                return ExfatBtimeSupport(
                    supported=None,
                    reason=f'mount failed: {msg[:120]}')

        test_file = os.path.join(mount_point, 'probe.bin')
        subprocess.run(['sudo', 'touch', test_file], capture_output=True, timeout=15)
        subprocess.run(['sudo', 'chmod', '644', test_file], capture_output=True, timeout=15)

        subprocess.run(['sync'])
        subprocess.run(['sudo', 'sh', '-c', 'echo 3 > /proc/sys/vm/drop_caches'],
                       capture_output=True, timeout=15)

        # Read btime via raw block — works on all kernels
        from strategies.exfat_raw import read_exfat_btime_raw
        btime_val = read_exfat_btime_raw(test_file)

        if btime_val is not None and btime_val > 0:
            return ExfatBtimeSupport(supported=True)
        reason = f'raw readback returned {btime_val}' if btime_val is not None else 'raw readback failed'
        return ExfatBtimeSupport(supported=False, reason=reason)

    except FileNotFoundError as e:
        return ExfatBtimeSupport(supported=None, reason=f'missing tool: {e.name}')
    except Exception as e:
        return ExfatBtimeSupport(supported=None, reason=str(e))
    finally:
        if mount_point and loop_dev:
            subprocess.run(['sudo', 'umount', mount_point],
                           capture_output=True, timeout=15)
        if loop_dev:
            subprocess.run(['sudo', 'losetup', '-d', loop_dev],
                           capture_output=True, timeout=15)
        if tmp_img and os.path.exists(tmp_img):
            os.unlink(tmp_img)
        if mount_point and os.path.exists(mount_point):
            os.rmdir(mount_point)


def probe_btime(path: str | Path) -> BtimeProbeResult:
    """Probe birth-time readability on the filesystem containing *path*."""
    path = str(path)
    try:
        fs_type = btime.detect_fs(path)
    except Exception:
        fs_type = None

    stat_btime = _probe_stat_btime(path)
    statx_btime, statx_supported = _probe_statx_btime(path)
    utime_works = _probe_utime(path)
    # statx_supported is None when syscall fails, else bool
    return BtimeProbeResult(
        path=path,
        fs_type=fs_type,
        stat_btime=stat_btime,
        statx_btime=statx_btime,
        statx_supported=statx_supported,
        utime_works=utime_works,
    )


# ── Main check ──────────────────────────────────────────────────────

def check_env(target_path: str | Path | None = None) -> EnvReport:
    """Probe the system and return an :class:`EnvReport`.

    If *target_path* is given, the filesystem type is detected
    so btime-method compatibility can be reported per-filesystem.
    """
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
        exfat_probe = _probe_exfat_btime()

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
        icon = '✓' if e.supported else ('✗' if e.supported is False else '?')
        lines.append('')
        lines.append(f'exFAT btime probe (temp filesystem):')
        lines.append(f'  Kernel exFAT btime readback: {icon}')
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
