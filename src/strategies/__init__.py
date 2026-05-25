from strategies.base import BtimeStrategy
from strategies.exfat_raw import ExfatRawStrategy, ExfatRawReadStrategy
from strategies.debugfs import DebugfsStrategy

REGISTRY: dict[str, type[BtimeStrategy]] = {
    ExfatRawStrategy.name: ExfatRawStrategy,
    ExfatRawReadStrategy.name: ExfatRawReadStrategy,
    DebugfsStrategy.name: DebugfsStrategy,
}

__all__ = [
    'BtimeStrategy',
    'ExfatRawStrategy',
    'ExfatRawReadStrategy',
    'DebugfsStrategy',
    'REGISTRY',
]
