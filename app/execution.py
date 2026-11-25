"""Provider-neutral, fail-closed demo execution and accounting."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from threading import RLock

from app.policy import TradingPolicy

ORDER_STATES = {"submitted", "accepted", "working", "partially_filled", "filled", "cancelled", "rejected"}
LEGAL_TRANSITIONS = {
    "submitted": {"accepted", "rejected", "cancelled"},
    "accepted": {"working", "rejected", "cancelled"},
    "working": {"partially_filled", "filled", "cancelled", "rejected"},
    "partially_filled": {"partially_filled", "filled", "cancelled"},
}


def utc_text(timestamp: datetime) -> str:
    if timestamp.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return timestamp.isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class AuditCard:
    card_id: str
    timestamp: datetime
    signal_id: str
    order_id: str
    event: str
    payload: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class Order:
    order_id: str
    signal_id: str
    symbol: str
    side: str
    quantity: Decimal
    order_type: str
    state: str
    submitted_at: datetime
    limit_price: Decimal | None = None
    reason: str | None = None

    def transition(self, state: str, *, timestamp: datetime, reason: str | None = None) -> Order:
        if state not in ORDER_STATES:
            raise ValueError(f"unknown order state: {state}")
        if state not in LEGAL_TRANSITIONS.get(self.state, set()) and state != self.state:
            raise ValueError(f"illegal order transition: {self.state}->{state}")
        return replace(self, state=state, reason=reason)


@dataclass(frozen=True, slots=True)
class Fill:
    fill_id: str
    order_id: str
    signal_id: str
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal
    fee: Decimal
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class Position:
    symbol: str
    quantity: Decimal
    average_cost: Decimal


@dataclass(frozen=True, slots=True)
class EquitySnapshot:
    timestamp: datetime
    cash: Decimal
    positions_value: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    equity: Decimal


class DemoBroker:
    """In-process paper broker; has no network or live-broker interface."""

    def __init__(
        self,
        policy: TradingPolicy,
        *,
        starting_cash: Decimal = Decimal(100000),
        fee_rate: Decimal = Decimal("0.0001"),
    ) -> None:
        mode = os.getenv("TRADING_MODE", "demo").strip().lower()
        if mode not in {"backtest", "demo", "offline"}:
            raise RuntimeError("live routing is prohibited; TRADING_MODE must be backtest, demo, or offline")
        if os.getenv("X2_LIVE_BROKER_URL") or os.getenv("LIVE_BROKER_URL"):
            raise RuntimeError("live broker environment is prohibited for demo execution")
        if policy.config["engine"].get("mode") != "demo":
            raise RuntimeError("demo broker requires demo policy mode")
        if starting_cash <= 0 or fee_rate < 0:
            raise ValueError("invalid starting cash or fee rate")
        self.policy = policy
        self.starting_cash = starting_cash
        self.fee_rate = fee_rate
        self.cash = starting_cash
        self.realized_pnl = Decimal(0)
        self._positions: dict[str, Position] = {}
        self._orders: dict[str, Order] = {}
        self._fills: dict[str, Fill] = {}
        self._audit: list[AuditCard] = []
        self._snapshots: list[EquitySnapshot] = []
        self._lock = RLock()

    def _card(self, timestamp: datetime, signal_id: str, order_id: str, event: str, payload: Mapping[str, object]) -> AuditCard:
        canonical = json.dumps({"timestamp": utc_text(timestamp), "signal_id": signal_id, "order_id": order_id, "event": event, "payload": payload}, sort_keys=True, default=str)
        card_id = hashlib.sha256(canonical.encode()).hexdigest()
        return AuditCard(card_id, timestamp, signal_id, order_id, event, dict(payload))

    def submit_signal(self, *, signal_id: str, symbol: str, side: str, quantity: Decimal,
                      timestamp: datetime, minutes_to_close: int | None = None,
                      session_open: bool = True, stale_feed_seconds: float = 0,
                      stale_threshold_seconds: float = 10, regime: object = None,
                      limit_price: Decimal | None = None) -> Order:
        if side not in {"buy", "sell"}:
            return self._rejected(signal_id, symbol, side, quantity, timestamp, "invalid_side")
        if quantity <= 0:
            return self._rejected(signal_id, symbol, side, quantity, timestamp, "invalid_quantity")
        if regime is None:
            return self._rejected(signal_id, symbol, side, quantity, timestamp, "missing_regime")
        if hasattr(regime, "value"):
            decision = self.policy.evaluate(timestamp=timestamp, symbol=symbol, regime=regime,
                stale_feed_seconds=stale_feed_seconds, stale_threshold_seconds=stale_threshold_seconds,
                session_open=session_open, minutes_to_close=minutes_to_close)
        else:
            return self._rejected(signal_id, symbol, side, quantity, timestamp, "invalid_regime")
        if not decision.allowed:
            return self._rejected(signal_id, symbol, side, quantity, timestamp,
                                  "policy:" + ",".join(decision.rejected_gates))

        market_name, market = self.policy.resolve_market(symbol)
        cluster = str(market.get("cluster", market_name))
        cap = Decimal(str(market.get("cluster_risk_cap", "0")))
        existing = sum((pos.quantity * self._mark_for(pos.symbol, timestamp) for pos in self._positions.values()
                        if self.policy.resolve_market(pos.symbol)[1].get("cluster", market_name) == cluster), Decimal(0))
        proposed = existing + quantity * Decimal(str(limit_price or 0))
        if cap > 0 and proposed > self.equity({symbol: limit_price or Decimal(1)}) * cap:
            return self._rejected(signal_id, symbol, side, quantity, timestamp, "correlation_cluster_cap")

        engine = self.policy.config["engine"]
        stop = Decimal(str(engine.get("session_stop_loss_pct", "0")))
        if stop > 0 and self.realized_pnl < -(self.starting_cash * stop):
            return self._rejected(signal_id, symbol, side, quantity, timestamp, "session_stop")

        order_id = hashlib.sha256(f"{signal_id}:{symbol}:{timestamp.isoformat()}:{side}:{quantity}".encode()).hexdigest()[:32]
        order = Order(order_id, signal_id, symbol, side, quantity, "limit" if limit_price is not None else "market", "submitted", timestamp, limit_price)
        with self._lock:
            if order_id in self._orders:
                return self._orders[order_id]
            self._orders[order_id] = order
            self._audit.append(self._card(timestamp, signal_id, order_id, "submitted", {"symbol": symbol, "side": side, "quantity": str(quantity)}))
        accepted = order.transition("accepted", timestamp=timestamp)
        working = accepted.transition("working", timestamp=timestamp)
        with self._lock:
            self._orders[order_id] = working
            self._audit.append(self._card(timestamp, signal_id, order_id, "accepted_working", {"state": "working"}))
        return working

    def _rejected(self, signal_id: str, symbol: str, side: str, quantity: Decimal, timestamp: datetime, reason: str) -> Order:
        order_id = hashlib.sha256(f"{signal_id}:{symbol}:{timestamp.isoformat()}:{side}:{quantity}:rejected".encode()).hexdigest()[:32]
        order = Order(order_id, signal_id, symbol, side, quantity, "market", "rejected", timestamp, reason=reason)
        with self._lock:
            self._orders[order_id] = order
            self._audit.append(self._card(timestamp, signal_id, order_id, "rejected", {"reason": reason}))
        return order

    def cancel(self, order_id: str, timestamp: datetime, reason: str = "cancelled") -> Order:
        with self._lock:
            order = self._orders[order_id]
            updated = order.transition("cancelled", timestamp=timestamp, reason=reason)
            self._orders[order_id] = updated
            self._audit.append(self._card(timestamp, order.signal_id, order_id, "cancelled", {"reason": reason}))
            return updated

    def flatten(self, *, timestamp: datetime, marks: Mapping[str, Decimal]) -> tuple[Fill, ...]:
        """Sell every demo position at supplied marks; never routes externally."""
        fills: list[Fill] = []
        for symbol, position in tuple(self._positions.items()):
            if position.quantity <= 0:
                continue
            price = marks.get(symbol)
            if price is None or price <= 0:
                raise ValueError(f"flat-mark required for {symbol}")
            signal_id = f"flatten:{timestamp.isoformat()}:{symbol}"
            order_id = hashlib.sha256(f"{signal_id}:{symbol}:flatten".encode()).hexdigest()[:32]
            order = Order(order_id, signal_id, symbol, "sell", position.quantity, "market", "working", timestamp)
            with self._lock:
                self._orders[order_id] = order
                self._audit.append(self._card(timestamp, signal_id, order_id, "submitted", {"flatten": True}))
            fills.append(self.fill(order_id, price=price, quantity=position.quantity, timestamp=timestamp))
        return tuple(fills)

    def fill(self, order_id: str, *, price: Decimal, quantity: Decimal, timestamp: datetime) -> Fill:
        if price <= 0 or quantity <= 0:
            raise ValueError("fill price and quantity must be positive")
        with self._lock:
            order = self._orders[order_id]
            fill_id = hashlib.sha256(f"{order_id}:{timestamp.isoformat()}:{quantity}:{price}".encode()).hexdigest()
            if fill_id in self._fills:
                return self._fills[fill_id]
            if order.state not in {"working", "partially_filled"}:
                raise ValueError(f"cannot fill order in state {order.state}")
            if quantity > order.quantity:
                raise ValueError("fill quantity exceeds order quantity")
            already = sum((f.quantity for f in self._fills.values() if f.order_id == order_id), Decimal(0))
            if already + quantity > order.quantity:
                raise ValueError("duplicate or overfill")
            fee = price * quantity * self.fee_rate
            fill = Fill(fill_id, order_id, order.signal_id, order.symbol, order.side, quantity, price, fee, timestamp)
            pos = self._positions.get(order.symbol, Position(order.symbol, Decimal(0), Decimal(0)))
            if order.side == "buy":
                new_qty = pos.quantity + quantity
                cost = pos.average_cost * pos.quantity + price * quantity
                new_avg = cost / new_qty if new_qty else Decimal(0)
                cost_and_fee = price * quantity + fee
                if cost_and_fee > self.cash:
                    raise ValueError("insufficient demo cash")
                self.cash -= cost_and_fee
                self._positions[order.symbol] = Position(order.symbol, new_qty, new_avg)
            else:
                new_qty = pos.quantity - quantity
                if new_qty < 0:
                    raise ValueError("cannot short in this demo adapter")
                proceeds = price * quantity - fee
                if pos.quantity:
                    self.realized_pnl += (price - pos.average_cost) * quantity - fee
                self.cash += proceeds
                self._positions[order.symbol] = Position(order.symbol, new_qty, pos.average_cost if new_qty else Decimal(0))
            self._fills[fill_id] = fill
            filled = already + quantity
            state = "filled" if filled == order.quantity else "partially_filled"
            updated = order.transition(state, timestamp=timestamp)
            self._orders[order_id] = updated
            self._audit.append(self._card(timestamp, order.signal_id, order_id, "fill", {"fill_id": fill_id, "quantity": str(quantity), "price": str(price), "fee": str(fee)}))
            return fill

    def _mark_for(self, symbol: str, timestamp: datetime) -> Decimal:
        position = self._positions.get(symbol)
        return position.average_cost if position is not None else Decimal(0)

    def equity(self, marks: Mapping[str, Decimal]) -> Decimal:
        unrealized = Decimal(0)
        with self._lock:
            for symbol, pos in self._positions.items():
                mark = marks.get(symbol, pos.average_cost)
                unrealized += (mark - pos.average_cost) * pos.quantity
            return self.cash + sum((marks.get(s, p.average_cost) * p.quantity for s, p in self._positions.items()), Decimal(0))

    def snapshot(self, timestamp: datetime, marks: Mapping[str, Decimal]) -> EquitySnapshot:
        positions_value = sum((marks.get(s, p.average_cost) * p.quantity for s, p in self._positions.items()), Decimal(0))
        unrealized = sum(((marks.get(s, p.average_cost) - p.average_cost) * p.quantity for s, p in self._positions.items()), Decimal(0))
        snapshot = EquitySnapshot(timestamp, self.cash, positions_value, unrealized, self.realized_pnl,
                                  self.cash + positions_value)
        with self._lock:
            self._snapshots.append(snapshot)
        return snapshot

    @property
    def orders(self) -> tuple[Order, ...]: return tuple(self._orders.values())
    @property
    def fills(self) -> tuple[Fill, ...]: return tuple(self._fills.values())
    @property
    def positions(self) -> tuple[Position, ...]: return tuple(p for p in self._positions.values() if p.quantity != 0)
    @property
    def audit_cards(self) -> tuple[AuditCard, ...]: return tuple(self._audit)
    @property
    def equity_snapshots(self) -> tuple[EquitySnapshot, ...]: return tuple(self._snapshots)
