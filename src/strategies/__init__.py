from strategies.base import BtimeStrategy
from strategies.exfat_raw import ExfatRawStrategy, ExfatRawReadStrategy
from strategies.debugfs import DebugfsStrategy
from strategies.fuse import FuseStrategy
from strategies.clock import ClockStrategy

REGISTRY: dict[str, type[BtimeStrategy]] = {
    ExfatRawStrategy.name: ExfatRawStrategy,
    ExfatRawReadStrategy.name: ExfatRawReadStrategy,
    DebugfsStrategy.name: DebugfsStrategy,
    FuseStrategy.name: FuseStrategy,
    ClockStrategy.name: ClockStrategy,
}

__all__ = [
    'BtimeStrategy',
    'ExfatRawStrategy',
    'ExfatRawReadStrategy',
    'DebugfsStrategy',
    'FuseStrategy',
    'ClockStrategy',
    'REGISTRY',
]
