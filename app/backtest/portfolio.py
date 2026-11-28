"""Deterministic shared-capital allocator for chronological backtest trade candidates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from math import floor


@dataclass(frozen=True, slots=True)
class PortfolioReservation:
    symbol: str
    market: str
    cluster: str
    exit_time: datetime
    notional: Decimal
    risk_amount: Decimal


@dataclass(frozen=True, slots=True)
class PortfolioDecision:
    accepted: bool
    quantity: Decimal
    risk_amount: Decimal
    notional: Decimal
    status: str
    reason: str | None = None


class PortfolioLedger:
    """Reserve shared cash, market allocation, cluster risk, and concurrency in event order."""

    def __init__(
        self,
        capital: Decimal,
        market_allocations: dict[str, Decimal],
        cluster_risk_caps: dict[str, Decimal],
        *,
        max_concurrent_positions: int = 10,
        max_total_open_risk_pct: Decimal = Decimal("0.01"),
    ) -> None:
        if capital <= 0 or max_concurrent_positions <= 0:
            raise ValueError("capital and max_concurrent_positions must be positive")
        if not Decimal(0) < max_total_open_risk_pct <= Decimal(1):
            raise ValueError("max_total_open_risk_pct must be in (0, 1]")
        if any(value < 0 or value > 1 for value in market_allocations.values()):
            raise ValueError("market allocation percentages must be in [0, 1]")
        if any(value < 0 or value > 1 for value in cluster_risk_caps.values()):
            raise ValueError("cluster risk caps must be in [0, 1]")
        self.capital = capital
        self.market_allocations = market_allocations
        self.cluster_risk_caps = cluster_risk_caps
        self.max_concurrent_positions = max_concurrent_positions
        self.max_total_open_risk_pct = max_total_open_risk_pct
        self.active: list[PortfolioReservation] = []

    def _release_expired(self, timestamp: datetime) -> None:
        self.active = [item for item in self.active if item.exit_time > timestamp]

    def reserve(
        self,
        *,
        timestamp: datetime,
        exit_time: datetime,
        symbol: str,
        market: str,
        cluster: str,
        requested_quantity: Decimal,
        entry_price: Decimal,
        stop_distance: Decimal,
        max_risk_amount: Decimal,
        whole_shares: bool = True,
    ) -> PortfolioDecision:
        self._release_expired(timestamp)
        if requested_quantity <= 0 or entry_price <= 0 or stop_distance <= 0 or max_risk_amount <= 0:
            return PortfolioDecision(False, Decimal(0), Decimal(0), Decimal(0), "rejected", "invalid_order_size")
        if len(self.active) >= self.max_concurrent_positions:
            return PortfolioDecision(False, Decimal(0), Decimal(0), Decimal(0), "rejected", "max_concurrent_positions")
        market_pct = self.market_allocations.get(market, Decimal(0))
        if market_pct <= 0:
            return PortfolioDecision(False, Decimal(0), Decimal(0), Decimal(0), "rejected", "market_allocation_disabled")
        cluster_pct = self.cluster_risk_caps.get(cluster, Decimal(0))
        if cluster_pct <= 0:
            return PortfolioDecision(False, Decimal(0), Decimal(0), Decimal(0), "rejected", "cluster_risk_cap_missing")

        open_notional = sum((item.notional for item in self.active), Decimal(0))
        market_notional = sum((item.notional for item in self.active if item.market == market), Decimal(0))
        cluster_risk = sum((item.risk_amount for item in self.active if item.cluster == cluster), Decimal(0))
        total_risk = sum((item.risk_amount for item in self.active), Decimal(0))
        cash_available = max(Decimal(0), self.capital - open_notional)
        market_available = max(Decimal(0), self.capital * market_pct - market_notional)
        cluster_risk_available = max(Decimal(0), self.capital * cluster_pct - cluster_risk)
        total_risk_available = max(
            Decimal(0), self.capital * self.max_total_open_risk_pct - total_risk
        )
        risk_budget = min(max_risk_amount, cluster_risk_available, total_risk_available)
        quantity = min(
            requested_quantity,
            risk_budget / stop_distance,
            cash_available / entry_price,
            market_available / entry_price,
        )
        if whole_shares:
            quantity = Decimal(floor(quantity))
        if quantity <= 0:
            reason = (
                "cluster_risk_cap" if cluster_risk_available <= 0 else
                "total_open_risk_cap" if total_risk_available <= 0 else
                "market_allocation" if market_available < entry_price else
                "available_cash"
            )
            return PortfolioDecision(False, Decimal(0), Decimal(0), Decimal(0), "rejected", reason)

        notional = quantity * entry_price
        risk_amount = quantity * stop_distance
        status = "accepted" if quantity == requested_quantity else "scaled"
        self.active.append(
            PortfolioReservation(symbol, market, cluster, exit_time, notional, risk_amount)
        )
        return PortfolioDecision(True, quantity, risk_amount, notional, status)
