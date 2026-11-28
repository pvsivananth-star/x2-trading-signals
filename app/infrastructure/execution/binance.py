"""Binance adapter boundary.

Live routing is intentionally unavailable in Phase 5. The adapter can only be
introduced after explicit live authorization and production certification.
"""

from app.domain.orders.model import Order, OrderReplacement
from app.application.execution import BrokerOrderUpdate


class BinanceExecutionAdapter:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("Binance live execution is disabled by policy")

