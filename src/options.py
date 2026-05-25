"""Shared options and constants for CLI, GUI, and computation modules.

Single source of truth for option values used across the entire application.
All user-facing option lists (CLI argparse choices, GUI combobox values) must
import from here instead of hardcoding strings, ensuring CLI and GUI stay in sync.
"""

# ── Btime (birth time) fix methods ─────────────────────────────────

BTIME_OFF = 'off'
BTIME_AUTO = 'auto'
BTIME_DEBUGFS = 'debugfs'
BTIME_EXFAT_RAW = 'exfat_raw'
BTIME_EXFAT_RAW_READ = 'exfat_raw_read'
# All valid btime method identifiers (including internal-only values).
ALL_BTIME_METHODS = frozenset({
    BTIME_OFF, BTIME_AUTO, BTIME_DEBUGFS,
    BTIME_EXFAT_RAW, BTIME_EXFAT_RAW_READ,
})

# User-facing choices for CLI argparse (excludes sentinel 'off').
BTIME_CLI_CHOICES = (
    BTIME_AUTO,
    BTIME_DEBUGFS,
    BTIME_EXFAT_RAW,
)

# Choices shown in the GUI combobox (includes 'off' to disable).
BTIME_GUI_CHOICES = (BTIME_OFF,) + BTIME_CLI_CHOICES

# Default ordered priority list for btime fallback chain.
BTIME_PRIORITY_ORDERED = (BTIME_AUTO, BTIME_EXFAT_RAW, BTIME_DEBUGFS)

# Methods that need per-file processing after mtime (e.g. raw block write).
BTIME_PROCESSING_AFTER = frozenset({BTIME_DEBUGFS, BTIME_EXFAT_RAW})


# ── Strategy options ───────────────────────────────────────────────

STRATEGY_GPS = 'gps'
STRATEGY_MANUAL = 'manual'
STRATEGY_SKIP = 'skip'

VALID_STRATEGIES = frozenset({STRATEGY_GPS, STRATEGY_MANUAL, STRATEGY_SKIP})
DEFAULT_STRATEGY = STRATEGY_MANUAL


# ── Calibration format strings ─────────────────────────────────────

CAL_DATE_FORMAT = 'YYYY-MM-DD'
CAL_TIME_FORMAT = 'HH:MM:SS.mmm'

# Human-readable labels for the GUI
CAL_DATE_LABEL = '  ISO: YYYY-MM-DD'
CAL_TIME_LABEL = '  HH:MM:SS.mmm (24h)'


# ── ExifTool server ───────────────────────────────────────────────────

EXIFTOOL_SERVER_PORT_FILE = 'gopro-exiftool-server.json'
"""Filename (in ``tempfile.gettempdir()``) storing the server's TCP port and PID."""

EXIFTOOL_SERVER_LOCK_FILE = 'gopro-exiftool-server.lock'
"""Lock file (same directory) for serialising concurrent ``_ensure_server()`` callers."""

EXIFTOOL_SERVER_IDLE_TIMEOUT = 60
"""Seconds of inactivity before the server auto-shuts down."""
