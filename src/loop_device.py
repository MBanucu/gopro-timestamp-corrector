"""Loop device setup/teardown — delegates to mount strategies."""

import subprocess

from strategies.mount import MountError as LoopDeviceError
from strategies.mount import ImageMountStrategy


def setup_loop_device(img_path: str) -> tuple[str, str]:
    """Set up loop device and mount an image.

    Tries udisksctl first (no sudo), then sudo losetup + sudo mount.
    Returns (loop_dev, mount_point).
    Raises LoopDeviceError on failure.
    """
    strategy = ImageMountStrategy(img_path)
    return strategy.mount()


def teardown_loop_device(loop_dev: str, mount_point: str | None = None):
    """Unmount and detach loop device."""
    if loop_dev:
        subprocess.run(['sudo', 'umount', loop_dev], capture_output=True)
        subprocess.run(['sudo', 'losetup', '-d', loop_dev], capture_output=True)
