import os
import subprocess
from datetime import datetime, timezone

from strategies.base import BtimeStrategy


class DebugfsStrategy(BtimeStrategy):
    name = 'debugfs'
    label = 'debugfs (ext4)'

    @classmethod
    def compatible_filesystems(cls) -> tuple[str, ...]:
        return ('ext2', 'ext3', 'ext4')

    @classmethod
    def needs_teardown(cls) -> bool:
        return True

    def setup(self, target_path, delta, dry_run):
        return {}

    def fix_file(self, filepath, dt, ctx, dry_run):
        from btime import _resolve_device

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

    def teardown(self, ctx, dry_run):
        pass
