from abc import ABC, abstractmethod
from datetime import datetime, timedelta


class BtimeStrategy(ABC):
    """Abstract base for a btime correction strategy.

    Each concrete strategy knows how to set up, fix files, and tear down
    for a specific btime method (exFAT raw block, debugfs, FUSE+faketime,
    system clock).
    """

    name: str
    label: str

    @classmethod
    @abstractmethod
    def compatible_filesystems(cls) -> tuple[str, ...]:
        """Filesystem types this strategy can work on."""

    @abstractmethod
    def setup(self, target_path: str, delta: timedelta, dry_run: bool) -> dict | None:
        """Prepare the environment for file correction.

        Returns a context dict (persisted across ``fix_file`` calls) or
        ``None`` to signal failure (caller falls back to next strategy).
        Strategies that need no setup return ``{}``.
        """

    @abstractmethod
    def fix_file(self, filepath: str, dt: datetime, ctx: dict, dry_run: bool):
        """Correct the birth time of *filepath* to *dt*."""

    @abstractmethod
    def teardown(self, ctx: dict, dry_run: bool):
        """Undo any permanent side effects from :meth:`setup`."""

    @classmethod
    def needs_setup(cls) -> bool:
        """Override and return True if this strategy needs :meth:`setup` called."""
        return False

    @classmethod
    def needs_teardown(cls) -> bool:
        """Override and return True if this strategy needs :meth:`teardown` called."""
        return False

    @classmethod
    def handles_mtime(cls) -> bool:
        """Override and return True if ``fix_file`` also updates mtime."""
        return False
