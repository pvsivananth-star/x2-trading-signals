"""Trade intent lifecycle; strategy intent is not an order."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

class TradeIntentStatus(StrEnum):
    CREATED="CREATED"
    PORTFOLIO_APPROVED="PORTFOLIO_APPROVED"
    RISK_APPROVED="RISK_APPROVED"
    REJECTED="REJECTED"
    CANCELLED="CANCELLED"
    EXPIRED="EXPIRED"

@dataclass(frozen=True, slots=True)
class TradeIntent:
    intent_id: str
    symbol: str
    side: str
    quantity: Decimal
    portfolio_id: str
    strategy_id: str
    strategy_version: str
    signal_id: str
    created_at: datetime
    limit_price: Decimal|None=None
    stop_price: Decimal|None=None
    target_price: Decimal|None=None
    status: TradeIntentStatus=TradeIntentStatus.CREATED
    reason_codes: tuple[str,...]=()
    reservation_id: str|None=None
    def __post_init__(self):
        if not self.intent_id or not self.signal_id or not self.symbol:
            raise ValueError("intent identity required")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.side not in {"BUY","SELL"}:
            raise ValueError("side must be BUY or SELL")
        if self.status is TradeIntentStatus.CREATED and not self.reason_codes:
            raise ValueError("trade intent requires reason codes")
