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


@dataclass(frozen=True, slots=True)
class ReplayResult:
    fills: tuple[Fill, ...]
    ending_cash: Decimal
    ending_quantity: Decimal
    ending_equity: Decimal


def replay_market_buys(
    bars: list[Bar],
    *,
    starting_cash: Decimal,
    quantity_per_fill: Decimal,
    fee_rate: Decimal = Decimal("0"),
    slippage_bps: Decimal = Decimal("0"),
) -> ReplayResult:
    """Buy one fixed quantity at each bar close, then mark the final position."""

    if starting_cash <= 0 or quantity_per_fill <= 0:
        raise ValueError("starting_cash and quantity_per_fill must be positive")
    if fee_rate < 0 or slippage_bps < 0:
        raise ValueError("fee_rate and slippage_bps cannot be negative")

    cash = starting_cash
    quantity = Decimal("0")
    fills: list[Fill] = []
    slippage = Decimal("1") + slippage_bps / Decimal("10000")

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

    final_price = chronological[-1].close if chronological else Decimal("0")
    equity = cash + quantity * final_price
    return ReplayResult(tuple(fills), cash, quantity, equity)
