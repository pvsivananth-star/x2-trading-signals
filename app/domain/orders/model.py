"""Canonical order lifecycle and immutable order state."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class OrderStatus(StrEnum):
    CREATED = "CREATED"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    WORKING = "WORKING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    REPLACE_PENDING = "REPLACE_PENDING"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


LEGAL_ORDER_TRANSITIONS = {
    OrderStatus.CREATED: {OrderStatus.SUBMITTED, OrderStatus.REJECTED},
    OrderStatus.SUBMITTED: {OrderStatus.ACCEPTED, OrderStatus.REJECTED, OrderStatus.UNKNOWN},
    OrderStatus.ACCEPTED: {OrderStatus.WORKING, OrderStatus.REJECTED, OrderStatus.CANCEL_PENDING, OrderStatus.REPLACE_PENDING, OrderStatus.UNKNOWN},
    OrderStatus.WORKING: {OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCEL_PENDING, OrderStatus.REPLACE_PENDING, OrderStatus.REJECTED, OrderStatus.UNKNOWN},
    OrderStatus.PARTIALLY_FILLED: {OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCEL_PENDING, OrderStatus.REPLACE_PENDING, OrderStatus.UNKNOWN},
    OrderStatus.CANCEL_PENDING: {OrderStatus.CANCELLED, OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED, OrderStatus.UNKNOWN},
    OrderStatus.REPLACE_PENDING: {OrderStatus.ACCEPTED, OrderStatus.WORKING, OrderStatus.REJECTED, OrderStatus.UNKNOWN},
    OrderStatus.FILLED: set(),
    OrderStatus.CANCELLED: set(),
    OrderStatus.REJECTED: set(),
    OrderStatus.UNKNOWN: {OrderStatus.ACCEPTED, OrderStatus.WORKING, OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED},
}


@dataclass(frozen=True, slots=True)
class Order:
    order_id: str
    client_order_id: str
    intent_id: str
    signal_id: str
    symbol: str
    side: str
    quantity: Decimal
    order_type: str
    status: OrderStatus
    created_at: datetime
    updated_at: datetime
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    filled_quantity: Decimal = Decimal("0")
    average_fill_price: Decimal | None = None
    broker_order_id: str | None = None
    reject_reason: str | None = None
    cancel_reason: str | None = None

    @property
    def remaining_quantity(self) -> Decimal:
        return self.quantity - self.filled_quantity

    def transition(self, status: OrderStatus, *, timestamp: datetime, **changes) -> "Order":
        if status != self.status and status not in LEGAL_ORDER_TRANSITIONS[self.status]:
            raise ValueError(f"illegal order transition: {self.status}->{status}")
        return replace(self, status=status, updated_at=timestamp, **changes)


@dataclass(frozen=True, slots=True)
class OrderReplacement:
    quantity: Decimal | None = None
    limit_price: Decimal | None = None

    def __post_init__(self):
        if self.quantity is not None and self.quantity <= 0:
            raise ValueError("replacement quantity must be positive")
        if self.limit_price is not None and self.limit_price <= 0:
            raise ValueError("replacement limit price must be positive")
