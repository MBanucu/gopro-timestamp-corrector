import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import timezone
from pathlib import Path


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


def detect_fs(path):
    result = subprocess.run(
        ['df', '--output=fstype', str(path)],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        lines = result.stdout.strip().splitlines()
        return lines[1].strip() if len(lines) >= 2 else None
    return None


def resolve_method(requested, fs_type):
    if requested == 'debugfs':
        return 'debugfs'
    elif requested == 'fuse':
        return 'fuse'
    elif requested == 'clock':
        return 'clock'
    if fs_type == 'ext4':
        return 'debugfs'
    elif fs_type in ('exfat', 'vfat', 'msdos'):
        return 'fuse'
    return 'clock'


def needs_processing_before(method):
    return method == 'fuse'


def needs_processing_after(method):
    return method == 'debugfs'


def setup(method, target_path, delta, dry_run):
    if method == 'fuse':
        return _setup_fuse(target_path, delta, dry_run)
    elif method == 'clock':
        return _setup_clock(dry_run)
    return {}


def teardown(method, ctx, dry_run):
    if method == 'fuse':
        _teardown_fuse(ctx, dry_run)
    elif method == 'clock':
        _teardown_clock(ctx, dry_run)


def fix_file(method, filepath, dt, ctx, dry_run):
    if method == 'debugfs':
        _fix_debugfs(filepath, dt, dry_run)
    elif method == 'clock':
        _fix_clock_set_time(dt, dry_run)


def _fix_debugfs(filepath, dt, dry_run):
    st = os.stat(filepath)
    device = _resolve_device(filepath)
    if not device:
        print(f"    ! Could not resolve device")
        return

    ts_sec = int(dt.replace(tzinfo=timezone.utc).timestamp())

    if dry_run:
        print(f"    Would set btime via debugfs on inode {st.st_ino}")
        return

    r1 = subprocess.run(['sudo', 'debugfs', '-w', device, '-R',
                         f'set_inode_field <{st.st_ino}> crtime_lo {ts_sec}'],
                        capture_output=True, text=True)
    r2 = subprocess.run(['sudo', 'debugfs', '-w', device, '-R',
                         f'set_inode_field <{st.st_ino}> crtime_extra 0'],
                        capture_output=True, text=True)

    if r1.returncode != 0:
        print(f"    \u2717  debugfs failed: {r1.stderr.strip()}")
        return

    subprocess.run(['sync'])
    subprocess.run(['sudo', 'sh', '-c', 'echo 3 > /proc/sys/vm/drop_caches'],
                   capture_output=True)
    print(f"    \u2713  btime corrected via debugfs")


def _setup_fuse(target_path, delta, dry_run):
    if not shutil.which('faketime'):
        print("  ! faketime not found. Install libfaketime or use --fix-btime clock.")
        return None
    if not shutil.which('mount.exfat-fuse'):
        print("  ! mount.exfat-fuse not found. Install exfat or use --fix-btime clock.")
        return None

    device = _resolve_device(target_path)
    if not device:
        print("  ! Could not resolve device. Falling back to clock method.")
        return None

    total_sec = int(delta.total_seconds())
    offset = str(total_sec)

    if dry_run:
        print(f"    Would unmount {target_path} and remount with FUSE + faketime")
        return {'device': device, 'offset': offset}

    result = subprocess.run(
        ['sudo', 'umount', str(target_path)],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        result = subprocess.run(
            ['sudo', 'umount', '-l', str(target_path)],
            capture_output=True, text=True
        )
    if result.returncode != 0:
        print(f"    ! Failed to unmount: {result.stderr.strip()}")
        return None

    proc = subprocess.Popen(
        ['sudo', 'faketime', '-f', offset, 'mount.exfat-fuse', device, str(target_path),
         '-o', 'allow_other', '-o', 'nonempty', '-o', 'auto_unmount'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    for _ in range(5000):
        if proc.poll() is not None:
            print(f"    ! FUSE mount failed")
            subprocess.run(['sudo', 'mount', device, str(target_path)], capture_output=True)
            return None
        if os.path.ismount(str(target_path)):
            break
        time.sleep(0.002)
    else:
        print(f"    ! FUSE mount timed out")
        subprocess.run(['sudo', 'mount', device, str(target_path)], capture_output=True)
        return None

    print(f"    \u2713  FUSE + faketime mounted ({delta})")
    return {'proc': proc, 'mount': target_path, 'device': device}


def _teardown_fuse(ctx, dry_run):
    if dry_run or not ctx:
        return
    mount = ctx.get('mount')
    proc = ctx.get('proc')
    if proc:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    if mount:
        r = subprocess.run(['sudo', 'umount', '-f', str(mount)], capture_output=True)
        if r.returncode != 0:
            subprocess.run(['sudo', 'umount', '-l', str(mount)], capture_output=True)
    print(f"    FUSE mount torn down.")


def _setup_clock(dry_run):
    if dry_run:
        print(f"    Would stop NTP (timedatectl set-ntp false)")
        return {'ntp_stopped': True}
    result = subprocess.run(
        ['sudo', 'timedatectl', 'set-ntp', 'false'],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"    ! Failed to stop NTP: {result.stderr.strip()}")
        return {}
    return {'ntp_stopped': True}


def _teardown_clock(ctx, dry_run):
    if dry_run or not ctx:
        return
    if ctx.get('ntp_stopped'):
        result = subprocess.run(
            ['sudo', 'timedatectl', 'set-ntp', 'true'],
            capture_output=True
        )
        if result.returncode == 0:
            print(f"    NTP restarted, clock syncing...")
        else:
            for cmd in [
                ['systemd-run', '--user', '--on-calendar', 'now', 'systemd-timesyncd'],
                ['sudo', 'ntpdate', '-u', 'pool.ntp.org'],
            ]:
                r = subprocess.run(cmd, capture_output=True, timeout=10)
                if r.returncode == 0:
                    break


def _fix_clock_set_time(dt, dry_run):
    ts = int(dt.replace(tzinfo=timezone.utc).timestamp())
    label = dt.strftime("%Y-%m-%d %H:%M:%S")
    if dry_run:
        print(f"    Would set clock to: {label}")
        return
    result = subprocess.run(
        ['sudo', 'date', '-s', f'@{ts}'],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f"    Clock set to: {label}")
    else:
        print(f"    ! Failed to set clock: {result.stderr.strip()}")
