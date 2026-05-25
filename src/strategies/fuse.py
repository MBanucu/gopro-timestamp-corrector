import os
import shutil
import subprocess
import time

from strategies.base import BtimeStrategy


class FuseStrategy(BtimeStrategy):
    name = 'fuse'
    label = 'FUSE + faketime (exFAT)'

    @classmethod
    def compatible_filesystems(cls) -> tuple[str, ...]:
        return ('exfat', 'vfat', 'msdos', 'fuseblk')

    @classmethod
    def required_tools(cls) -> tuple[str, ...]:
        return ('faketime', 'mount.exfat-fuse', 'sudo', 'umount', 'mount', 'findmnt')

    @classmethod
    def needs_setup(cls) -> bool:
        return True

    @classmethod
    def needs_teardown(cls) -> bool:
        return True

    def setup(self, target_path, delta, dry_run):
        from btime import _resolve_device, _resolve_mount_point

        if not shutil.which('faketime'):
            print("  ! faketime not found. Install libfaketime to use FUSE + faketime.")
            return None
        if not shutil.which('mount.exfat-fuse'):
            print("  ! mount.exfat-fuse not found. Install exfat-fuse to use FUSE + faketime.")
            return None

        if not os.path.exists(target_path):
            print("  ! Path does not exist.")
            return None

        device = _resolve_device(target_path)
        if not device:
            print("  ! Could not resolve device.")
            return None

        mount_point = _resolve_mount_point(target_path)
        if not mount_point:
            print(f"  ! Could not resolve mount point for {target_path}.")
            return None

        total_sec = int(delta.total_seconds())
        offset = f'+{total_sec}' if total_sec >= 0 else str(total_sec)

        if dry_run:
            print(f"    Would unmount {mount_point} and remount with FUSE + faketime")
            return {'device': device, 'offset': offset}

        result = subprocess.run(
            ['sudo', 'umount', mount_point],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            result = subprocess.run(
                ['sudo', 'umount', '-l', mount_point],
                capture_output=True, text=True
            )
        if result.returncode != 0:
            print(f"    ! Failed to unmount: {result.stderr.strip()}")
            return None

        subprocess.run(['sudo', 'mkdir', '-p', mount_point], capture_output=True)

        uid = os.getuid()
        gid = os.getgid()

        proc = subprocess.Popen(
            ['sudo', 'faketime', '-f', offset, 'mount.exfat-fuse', device, mount_point,
             '-o', f'uid={uid}', '-o', f'gid={gid}',
             '-o', 'allow_other', '-o', 'nonempty', '-o', 'auto_unmount'],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
        )

        for _ in range(5000):
            if proc.poll() is not None:
                err = proc.stderr.read().strip() if proc.stderr else ''
                print(f"    ! FUSE mount failed: {err}")
                subprocess.run(['sudo', 'mkdir', '-p', mount_point], capture_output=True)
                r = subprocess.run(['sudo', 'mount', device, mount_point], capture_output=True, text=True)
                if r.returncode != 0:
                    print(f"    ! Failed to remount kernel: {r.stderr.strip()}")
                return None
            if os.path.ismount(mount_point):
                break
            time.sleep(0.002)
        else:
            err = proc.stderr.read().strip() if proc.stderr else ''
            print(f"    ! FUSE mount timed out: {err}")
            subprocess.run(['sudo', 'mkdir', '-p', mount_point], capture_output=True)
            r = subprocess.run(['sudo', 'mount', device, mount_point], capture_output=True, text=True)
            if r.returncode != 0:
                print(f"    ! Failed to remount kernel: {r.stderr.strip()}")
            return None

        print(f"    \u2713  FUSE + faketime mounted ({delta})")
        if proc.stderr:
            proc.stderr.close()
        return {'proc': proc, 'mount': mount_point, 'device': device}

    def fix_file(self, filepath, dt, ctx, dry_run):
        pass

    def teardown(self, ctx, dry_run):
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
            device = ctx.get('device')
            if device:
                subprocess.run(['sudo', 'mkdir', '-p', mount], capture_output=True)
                subprocess.run(['sudo', 'mount', device, mount], capture_output=True)
        print(f"    FUSE mount torn down.")
