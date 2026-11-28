"""Explicit runtime modes; live routing remains opt-in and disabled by default."""
from enum import StrEnum
class RuntimeMode(StrEnum):
    BACKTEST="backtest"
    PAPER="paper"
    DEMO="demo"
    OFFLINE="offline"
    LIVE_DISABLED="live_disabled"
    @property
    def allows_order_submission(self) -> bool:
        return self in {RuntimeMode.PAPER, RuntimeMode.DEMO}
    @property
    def allows_live_broker(self) -> bool:
        return False
