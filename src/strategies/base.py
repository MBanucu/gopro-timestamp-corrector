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

    @classmethod
    @abstractmethod
    def required_tools(cls) -> tuple[str, ...]:
        """External executables this strategy depends on (e.g. ``'sudo'``, ``'dd'``).

        The caller is expected to check availability via ``shutil.which``.
        Strategies whose tools are missing will be pruned by
        :func:`btime.viable_methods`.
        """

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

    @classmethod
    def requires_sudo(cls) -> bool:
        """Override and return False for strategies that never call ``sudo``."""
        return True

    @classmethod
    def is_internal(cls) -> bool:
        """Return True for strategies that should NOT appear in auto-detected lists.

        Internal strategies are registered in ``REGISTRY`` and are usable
        by name, but are excluded from :func:`btime.compatible_methods`
        and :func:`btime.viable_methods`.  Use this for strategies that
        are functionally identical to another (e.g. ``exfat_raw_read``
        which adds readback on top of ``exfat_raw``).
        """
        return False

    @classmethod
    def check_capabilities(
        cls,
        tools_available: frozenset[str],
        sudo_available: bool,
    ) -> bool:
        """Return True when the system can run this strategy.

        Checks tool availability and sudo — subclasses may override
        to add further probes (e.g. exFAT btime readback).
        """
        if cls.requires_sudo() and not sudo_available:
            return False
        return all(t in tools_available for t in cls.required_tools())
