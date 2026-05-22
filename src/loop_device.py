"""Loop device setup/teardown — delegates to mount strategies."""

import subprocess

from strategies.mount import MountError as LoopDeviceError
from strategies.mount import ImageMountStrategy


def _check_dio(loop_dev: str):
    try:
        r = subprocess.run(
            ['cat', f'/sys/block/{loop_dev.removeprefix("/dev/")}/loop/dio'],
            capture_output=True, text=True, timeout=5)
        val = r.stdout.strip()
        print(f"[dbg] loop dio: {loop_dev}={val}")
    except Exception as e:
        print(f"[dbg] loop dio: {loop_dev}=error({e})")


def setup_loop_device(img_path: str) -> tuple[str, str]:
    """Set up loop device and mount an image.

    Tries udisksctl first (no sudo), then sudo losetup + sudo mount.
    Returns (loop_dev, mount_point).
    Raises LoopDeviceError on failure.
    """
    strategy = ImageMountStrategy(img_path)
    result = strategy.mount()
    _check_dio(result[0])
    return result


def teardown_loop_device(loop_dev: str, mount_point: str | None = None):
    """Unmount and detach loop device."""
    if loop_dev:
        subprocess.run(['sudo', 'umount', loop_dev], capture_output=True)
        subprocess.run(['sudo', 'losetup', '-d', loop_dev], capture_output=True)
