"""Canonical fill model; fills are the source of economic position truth."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Fill:
    fill_id: str
    order_id: str
    intent_id: str
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal
    fee: Decimal
    timestamp: datetime
    broker_fill_id: str | None = None

    def __post_init__(self):
        if not self.fill_id or not self.order_id or not self.intent_id:
            raise ValueError("fill identity required")
        if self.quantity <= 0 or self.price <= 0 or self.fee < 0:
            raise ValueError("invalid fill economics")
