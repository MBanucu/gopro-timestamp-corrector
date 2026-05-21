import subprocess
from datetime import datetime, timezone

from strategies.base import BtimeStrategy


class ClockStrategy(BtimeStrategy):
    name = 'clock'
    label = 'System clock'

    @classmethod
    def compatible_filesystems(cls) -> tuple[str, ...]:
        return ('unknown',)

    @classmethod
    def required_tools(cls) -> tuple[str, ...]:
        return ('timedatectl', 'date', 'sudo')

    @classmethod
    def needs_setup(cls) -> bool:
        return True

    @classmethod
    def needs_teardown(cls) -> bool:
        return True

    def setup(self, target_path, delta, dry_run):
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

    def fix_file(self, filepath, dt, ctx, dry_run):
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

    def teardown(self, ctx, dry_run):
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
