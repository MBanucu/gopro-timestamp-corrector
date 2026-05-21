"""exFAT raw-block strategy with btime readback capability.

Extends :class:`ExfatRawStrategy` with a :meth:`read_btime_raw` method
that reads birth time directly from the on‑disk exFAT directory entry,
bypassing the kernel's ``statx`` / ``stat`` interface.  This makes it
usable as a capability probe on **all** kernel versions, even those
(<6.12) where the exFAT driver does not advertise ``STATX_BTIME``.
"""

from strategies.exfat_raw import (
    ExfatRawStrategy,
    _exfat_decode_time,
    _exfat_find_file_entry,
    _exfat_parse_boot,
)


class ExfatRawReadStrategy(ExfatRawStrategy):
    """exFAT raw-block btime correction with on‑disk readback.

    Identical to :class:`ExfatRawStrategy` for writing, but additionally
    exposes :meth:`read_btime_raw` for reading birth time from the raw
    block device.
    """

    name = 'exfat_raw_read'
    label = 'exFAT raw block (readback)'

    @classmethod
    def is_internal(cls) -> bool:
        return True

    @staticmethod
    def read_btime_raw(filepath: str) -> int | None:
        """Read birth time from *filepath* via raw block access.

        Returns epoch seconds (UTC), or ``None`` if the file or its
        device cannot be resolved.
        """
        import struct
        from datetime import timezone
        from btime import _resolve_device

        device = _resolve_device(filepath)
        if not device:
            return None

        boot = _exfat_parse_boot(device)
        if not boot:
            return None

        entry = _exfat_find_file_entry(boot, device, filepath)
        if entry is None:
            return None

        time_word = struct.unpack_from('<H', entry, 0x0C)[0]
        date_word = struct.unpack_from('<H', entry, 0x0E)[0]
        time_ms = entry[0x14]

        dt = _exfat_decode_time(time_word, date_word, time_ms)
        return int(dt.replace(tzinfo=timezone.utc).timestamp())
