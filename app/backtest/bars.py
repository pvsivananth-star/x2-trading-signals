"""Deterministic trade-to-bar rollups for replay and backtesting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Trade:
    event_time: datetime
    price: Decimal
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class Bar:
    bar_start: datetime
    bar_end: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trade_count: int


def rollup(trades: list[Trade], interval_seconds: int) -> list[Bar]:
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")

    grouped: dict[datetime, list[Trade]] = {}
    interval = timedelta(seconds=interval_seconds)
    for trade in trades:
        timestamp = trade.event_time.astimezone(UTC)
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        elapsed = timestamp - epoch
        bucket_seconds = (elapsed.days * 86400 + elapsed.seconds) // interval_seconds * interval_seconds
        bucket = epoch + timedelta(seconds=bucket_seconds)
        grouped.setdefault(bucket, []).append(trade)

    bars: list[Bar] = []
    for start in sorted(grouped):
        bucket_trades = sorted(grouped[start], key=lambda item: item.event_time)
        prices = [trade.price for trade in bucket_trades]
        bars.append(Bar(
            bar_start=start,
            bar_end=start + interval,
            open=prices[0],
            high=max(prices),
            low=min(prices),
            close=prices[-1],
            volume=sum((trade.quantity for trade in bucket_trades), Decimal(0)),
            trade_count=len(bucket_trades),
        ))
    return bars
