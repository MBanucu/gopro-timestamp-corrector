"""BtimeStrategy adapters for exFAT raw-block correction."""

from strategies.base import BtimeStrategy


class ExfatRawStrategy(BtimeStrategy):
    name = 'exfat_raw'
    label = 'exFAT raw block'

    def __init__(self, ops=None):
        from strategies.exfat_raw import exfat_ops
        self._ops = ops or exfat_ops

    @classmethod
    def compatible_filesystems(cls) -> tuple[str, ...]:
        return ('exfat', 'vfat', 'msdos', 'fuseblk')

    @classmethod
    def required_tools(cls) -> tuple[str, ...]:
        return ('dd', 'findmnt', 'sudo', 'sync', 'mount', 'umount')

    @classmethod
    def needs_teardown(cls) -> bool:
        return True

    @classmethod
    def handles_mtime(cls) -> bool:
        return True

    def setup(self, target_path, delta, dry_run):
        return {}

    def fix_file(self, filepath, dt, ctx, dry_run):
        self._ops.fix_exfat_raw(filepath, dt, dry_run)

    def teardown(self, ctx, dry_run):
        pass


class ExfatRawReadStrategy(ExfatRawStrategy):
    """exFAT raw-block btime correction with on-disk readback."""

    name = 'exfat_raw_read'
    label = 'exFAT raw block (readback)'

    @classmethod
    def is_internal(cls) -> bool:
        return True

    def read_btime_raw(self, filepath: str) -> int | None:
        return self._ops.read_btime_raw(filepath)
