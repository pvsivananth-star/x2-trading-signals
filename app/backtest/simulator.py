"""Deterministic local replay/simulation primitives."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.backtest.bars import Bar


@dataclass(frozen=True, slots=True)
class Fill:
    bar_start: object
    side: str
    quantity: Decimal
    fill_price: Decimal
    fee: Decimal
    status: str = "filled"


@dataclass(frozen=True, slots=True)
class ReplayResult:
    fills: tuple[Fill, ...]
    ending_cash: Decimal
    ending_quantity: Decimal
    ending_equity: Decimal


@dataclass(frozen=True, slots=True)
class SimulationConfig:
    spread_bps: Decimal = Decimal(0)
    slippage_bps: Decimal = Decimal(0)
    fee_rate: Decimal = Decimal(0)
    partial_fill_ratio: Decimal = Decimal(1)


class OrderSimulator:
    """Small deterministic market/limit fill engine for replay runs."""

    def __init__(self, config: SimulationConfig | None = None) -> None:
        self.config = config or SimulationConfig()
        if self.config.spread_bps < 0 or self.config.slippage_bps < 0 or self.config.fee_rate < 0:
            raise ValueError("spread, slippage, and fee rates cannot be negative")
        if self.config.partial_fill_ratio <= 0 or self.config.partial_fill_ratio > 1:
            raise ValueError("partial_fill_ratio must be in (0, 1]")

    def submit(
        self,
        bar: Bar,
        side: str,
        order_type: str,
        quantity: Decimal,
        limit_price: Decimal | None = None,
    ) -> Fill:
        if side not in {"buy", "sell"} or order_type not in {"market", "limit"}:
            return Fill(bar.bar_start, side, Decimal(0), Decimal(0), Decimal(0), "rejected")
        if quantity <= 0:
            return Fill(bar.bar_start, side, Decimal(0), Decimal(0), Decimal(0), "rejected")
        if order_type == "limit":
            if limit_price is None:
                return Fill(bar.bar_start, side, Decimal(0), Decimal(0), Decimal(0), "rejected")
            if side == "buy" and bar.low > limit_price:
                return Fill(bar.bar_start, side, Decimal(0), Decimal(0), Decimal(0), "rejected")
            if side == "sell" and bar.high < limit_price:
                return Fill(bar.bar_start, side, Decimal(0), Decimal(0), Decimal(0), "rejected")

        ratio = self.config.partial_fill_ratio
        filled_qty = quantity * ratio
        reference = bar.open
        if side == "buy":
            base = reference * (Decimal(1) + self.config.spread_bps / Decimal(20000))
        else:
            base = reference * (Decimal(1) - self.config.spread_bps / Decimal(20000))
        slippage = self.config.slippage_bps / Decimal(10000)
        fill_price = base * (Decimal(1) + slippage if side == "buy" else Decimal(1) - slippage)
        if order_type == "limit" and limit_price is not None:
            fill_price = (
                min(fill_price, limit_price) if side == "buy" else max(fill_price, limit_price)
            )
        notional = fill_price * filled_qty
        fee = notional * self.config.fee_rate
        status = "partial" if ratio < 1 else "filled"
        return Fill(bar.bar_start, side, filled_qty, fill_price, fee, status)


def replay_market_buys(
    bars: list[Bar],
    *,
    starting_cash: Decimal,
    quantity_per_fill: Decimal,
    fee_rate: Decimal = Decimal(0),
    slippage_bps: Decimal = Decimal(0),
) -> ReplayResult:
    """Buy one fixed quantity at each bar close, then mark the final position."""
    if starting_cash <= 0 or quantity_per_fill <= 0:
        raise ValueError("starting_cash and quantity_per_fill must be positive")
    if fee_rate < 0 or slippage_bps < 0:
        raise ValueError("fee_rate and slippage_bps cannot be negative")

    cash = starting_cash
    quantity = Decimal(0)
    fills: list[Fill] = []
    slippage = Decimal(1) + slippage_bps / Decimal(10000)
    chronological = sorted(bars, key=lambda item: item.bar_start)
    for bar in chronological:
        fill_price = bar.close * slippage
        notional = fill_price * quantity_per_fill
        fee = notional * fee_rate
        if cash < notional + fee:
            break
        cash -= notional + fee
        quantity += quantity_per_fill
        fills.append(Fill(bar.bar_start, "buy", quantity_per_fill, fill_price, fee))

    final_price = chronological[-1].close if chronological else Decimal(0)
    equity = cash + quantity * final_price
    return ReplayResult(tuple(fills), cash, quantity, equity)
