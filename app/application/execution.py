"""Execution Manager: the only application boundary allowed to submit orders."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from threading import RLock
from typing import Any, Protocol

from app.domain.orders.model import Order, OrderReplacement, OrderStatus
from app.domain.risk.model import RiskDecisionStatus
from app.domain.runtime import RuntimeMode
from app.domain.trades.model import TradeIntent


@dataclass(frozen=True, slots=True)
class BrokerOrderUpdate:
    order_id: str
    status: OrderStatus
    timestamp: datetime
    broker_order_id: str | None = None
    filled_quantity: Decimal = Decimal("0")
    average_fill_price: Decimal | None = None
    reject_reason: str | None = None


@dataclass(frozen=True, slots=True)
class BrokerFill:
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


class ExecutionAdapter(Protocol):
    def submit(self, order: Order, idempotency_key: str) -> BrokerOrderUpdate: ...
    def cancel(self, order: Order, idempotency_key: str) -> BrokerOrderUpdate: ...
    def replace(self, order: Order, replacement: OrderReplacement, idempotency_key: str) -> BrokerOrderUpdate: ...
    def reconcile(self) -> tuple[BrokerOrderUpdate, ...]: ...


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    order: Order
    accepted: bool
    reason: str


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    healthy: bool
    orders: tuple[Order, ...]
    discrepancies: tuple[str, ...]


class ExecutionManager:
    def __init__(self, adapter: ExecutionAdapter, *, runtime_mode: RuntimeMode):
        self.adapter = adapter
        self.runtime_mode = runtime_mode
        self._orders: dict[str, Order] = {}
        self._idempotency: dict[str, str] = {}
        self._lock = RLock()
        self._reconciled = True

    @property
    def reconciled(self) -> bool:
        return self._reconciled

    @property
    def orders(self) -> tuple[Order, ...]:
        return tuple(self._orders.values())

    def _client_order_id(self, intent: TradeIntent) -> str:
        return "X2-" + sha256(intent.intent_id.encode()).hexdigest()[:24]

    def submit(self, intent: TradeIntent, *, portfolio_approved: bool, risk_status: RiskDecisionStatus,
               kill_switch_active: bool = False, timestamp: datetime) -> ExecutionResult:
        if not self.runtime_mode.allows_order_submission:
            return ExecutionResult(self._existing_or_rejected(intent, timestamp, "ORDER_SUBMISSION_DISABLED"), False, "ORDER_SUBMISSION_DISABLED")
        if kill_switch_active:
            return ExecutionResult(self._existing_or_rejected(intent, timestamp, "KILL_SWITCH_ACTIVE"), False, "KILL_SWITCH_ACTIVE")
        if not self._reconciled:
            return ExecutionResult(self._existing_or_rejected(intent, timestamp, "RECONCILIATION_REQUIRED"), False, "RECONCILIATION_REQUIRED")
        if not portfolio_approved:
            return ExecutionResult(self._existing_or_rejected(intent, timestamp, "PORTFOLIO_REJECTED"), False, "PORTFOLIO_REJECTED")
        if risk_status is not RiskDecisionStatus.APPROVED:
            return ExecutionResult(self._existing_or_rejected(intent, timestamp, "RISK_REJECTED"), False, "RISK_REJECTED")

        client_id = self._client_order_id(intent)
        with self._lock:
            existing_id = self._idempotency.get(intent.intent_id)
            if existing_id:
                return ExecutionResult(self._orders[existing_id], True, "IDEMPOTENT_REPLAY")
            order = Order(
                order_id=client_id,
                client_order_id=client_id,
                intent_id=intent.intent_id,
                signal_id=intent.signal_id,
                symbol=intent.symbol,
                side=intent.side,
                quantity=intent.quantity,
                order_type="LIMIT" if intent.limit_price is not None else "MARKET",
                status=OrderStatus.CREATED,
                created_at=timestamp,
                updated_at=timestamp,
                limit_price=intent.limit_price,
                stop_price=intent.stop_price,
            )
            self._orders[order.order_id] = order
            self._idempotency[intent.intent_id] = order.order_id

        try:
            submitted = order.transition(OrderStatus.SUBMITTED, timestamp=timestamp)
            self._orders[order.order_id] = submitted
            update = self.adapter.submit(submitted, intent.intent_id)
            updated = self.apply_update(update)
            return ExecutionResult(updated, updated.status not in {OrderStatus.REJECTED, OrderStatus.UNKNOWN}, updated.status.value)
        except Exception:
            with self._lock:
                current = self._orders[order.order_id]
                self._orders[order.order_id] = current.transition(OrderStatus.UNKNOWN, timestamp=timestamp)
                self._reconciled = False
            return ExecutionResult(self._orders[order.order_id], False, "BROKER_UNCERTAIN_RECONCILIATION_REQUIRED")

    def cancel(self, order_id: str, *, timestamp: datetime) -> ExecutionResult:
        order = self._orders[order_id]
        if order.status not in {OrderStatus.ACCEPTED, OrderStatus.WORKING, OrderStatus.PARTIALLY_FILLED}:
            return ExecutionResult(order, False, "ORDER_NOT_CANCELLABLE")
        pending = order.transition(OrderStatus.CANCEL_PENDING, timestamp=timestamp)
        self._orders[order_id] = pending
        try:
            return ExecutionResult(self.apply_update(self.adapter.cancel(pending, f"cancel:{order_id}")), True, "CANCEL_REQUESTED")
        except Exception:
            self._orders[order_id] = pending.transition(OrderStatus.UNKNOWN, timestamp=timestamp)
            self._reconciled = False
            return ExecutionResult(self._orders[order_id], False, "CANCEL_UNCERTAIN_RECONCILIATION_REQUIRED")

    def replace(self, order_id: str, replacement: OrderReplacement, *, timestamp: datetime) -> ExecutionResult:
        order = self._orders[order_id]
        if order.status not in {OrderStatus.ACCEPTED, OrderStatus.WORKING, OrderStatus.PARTIALLY_FILLED}:
            return ExecutionResult(order, False, "ORDER_NOT_REPLACEABLE")
        if replacement.quantity is not None and replacement.quantity < order.filled_quantity:
            return ExecutionResult(order, False, "REPLACEMENT_BELOW_FILLED_QUANTITY")
        pending = order.transition(OrderStatus.REPLACE_PENDING, timestamp=timestamp)
        self._orders[order_id] = pending
        try:
            return ExecutionResult(self.apply_update(self.adapter.replace(pending, replacement, f"replace:{order_id}:{timestamp.isoformat()}")), True, "REPLACE_REQUESTED")
        except Exception:
            self._orders[order_id] = pending.transition(OrderStatus.UNKNOWN, timestamp=timestamp)
            self._reconciled = False
            return ExecutionResult(self._orders[order_id], False, "REPLACE_UNCERTAIN_RECONCILIATION_REQUIRED")

    def apply_update(self, update: BrokerOrderUpdate) -> Order:
        with self._lock:
            order = self._orders[update.order_id]
            if update.filled_quantity < order.filled_quantity or update.filled_quantity > order.quantity:
                raise ValueError("invalid cumulative fill quantity")
            status = update.status
            if update.filled_quantity == order.quantity and status in {OrderStatus.WORKING, OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED}:
                status = OrderStatus.FILLED
            elif update.filled_quantity > 0 and status == OrderStatus.WORKING:
                status = OrderStatus.PARTIALLY_FILLED
            changes: dict[str, Any] = {
                "broker_order_id": update.broker_order_id or order.broker_order_id,
                "filled_quantity": update.filled_quantity,
                "average_fill_price": update.average_fill_price or order.average_fill_price,
                "reject_reason": update.reject_reason or order.reject_reason,
            }
            updated = order.transition(status, timestamp=update.timestamp, **changes)
            self._orders[order.order_id] = updated
            return updated

    def reconcile(self) -> ReconciliationResult:
        try:
            updates = self.adapter.reconcile()
        except Exception:
            self._reconciled = False
            return ReconciliationResult(False, self.orders, ("BROKER_RECONCILIATION_FAILED",))
        discrepancies: list[str] = []
        seen: set[str] = set()
        for update in updates:
            seen.add(update.order_id)
            if update.order_id not in self._orders:
                discrepancies.append(f"UNKNOWN_BROKER_ORDER:{update.order_id}")
                continue
            try:
                self.apply_update(update)
            except Exception as exc:
                discrepancies.append(f"ORDER_STATE_MISMATCH:{update.order_id}:{exc}")
        for order in self.orders:
            if order.status in {OrderStatus.SUBMITTED, OrderStatus.ACCEPTED, OrderStatus.WORKING, OrderStatus.PARTIALLY_FILLED, OrderStatus.UNKNOWN} and order.order_id not in seen:
                discrepancies.append(f"MISSING_BROKER_ORDER:{order.order_id}")
        self._reconciled = not discrepancies
        return ReconciliationResult(self._reconciled, self.orders, tuple(discrepancies))

    def _existing_or_rejected(self, intent: TradeIntent, timestamp: datetime, reason: str) -> Order:
        with self._lock:
            existing = self._idempotency.get(intent.intent_id)
            if existing:
                return self._orders[existing]
            order = Order(
                order_id=self._client_order_id(intent),
                client_order_id=self._client_order_id(intent),
                intent_id=intent.intent_id,
                signal_id=intent.signal_id,
                symbol=intent.symbol,
                side=intent.side,
                quantity=intent.quantity,
                order_type="LIMIT" if intent.limit_price is not None else "MARKET",
                status=OrderStatus.REJECTED,
                created_at=timestamp,
                updated_at=timestamp,
                limit_price=intent.limit_price,
                stop_price=intent.stop_price,
                reject_reason=reason,
            )
            self._orders[order.order_id] = order
            self._idempotency[intent.intent_id] = order.order_id
            return order


class InMemoryExecutionAdapter:
    """Deterministic paper/backtest adapter used for tests and replay."""

    def __init__(self):
        self.orders: dict[str, Order] = {}
        self.calls: list[str] = []

    def submit(self, order: Order, idempotency_key: str) -> BrokerOrderUpdate:
        if idempotency_key in self.calls:
            existing = self.orders[order.order_id]
            return BrokerOrderUpdate(existing.order_id, existing.status, existing.updated_at)
        self.calls.append(idempotency_key)
        accepted = order.transition(OrderStatus.ACCEPTED, timestamp=order.updated_at)
        self.orders[order.order_id] = accepted
        return BrokerOrderUpdate(accepted.order_id, OrderStatus.WORKING, accepted.updated_at)

    def cancel(self, order: Order, idempotency_key: str) -> BrokerOrderUpdate:
        self.calls.append(idempotency_key)
        return BrokerOrderUpdate(order.order_id, OrderStatus.CANCELLED, order.updated_at, broker_order_id=order.broker_order_id)

    def replace(self, order: Order, replacement: OrderReplacement, idempotency_key: str) -> BrokerOrderUpdate:
        self.calls.append(idempotency_key)
        return BrokerOrderUpdate(order.order_id, OrderStatus.WORKING, order.updated_at, broker_order_id=order.broker_order_id)

    def reconcile(self) -> tuple[BrokerOrderUpdate, ...]:
        return tuple(BrokerOrderUpdate(o.order_id, o.status, o.updated_at, o.broker_order_id, o.filled_quantity, o.average_fill_price, o.reject_reason) for o in self.orders.values())
