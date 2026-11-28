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
    final: bool = True
    partial: bool = False


def rollup(
    trades: list[Trade],
    interval_seconds: int,
    *,
    include_empty: bool = False,
    start: datetime | None = None,
    end: datetime | None = None,
    session_calendar: object | None = None,
) -> list[Bar]:
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")

    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    interval = timedelta(seconds=interval_seconds)
    grouped: dict[datetime, list[Trade]] = {}
    for trade in trades:
        timestamp = trade.event_time.astimezone(UTC)
        elapsed = timestamp - epoch
        bucket_seconds = (elapsed.days * 86400 + elapsed.seconds) // interval_seconds * interval_seconds
        bucket = epoch + timedelta(seconds=bucket_seconds)
        grouped.setdefault(bucket, []).append(trade)

    ordered_starts = sorted(grouped)
    if include_empty:
        range_start = start.astimezone(UTC) if start is not None else (ordered_starts[0] if ordered_starts else None)
        range_end = end.astimezone(UTC) if end is not None else (ordered_starts[-1] + interval if ordered_starts else None)
        if range_start is not None and range_end is not None:
            elapsed = range_start - epoch
            first = epoch + timedelta(seconds=(elapsed.days * 86400 + elapsed.seconds) // interval_seconds * interval_seconds)
            ordered_starts = []
            cursor = first
            while cursor < range_end:
                if session_calendar is None:
                    ordered_starts.append(cursor)
                elif hasattr(session_calendar, "is_session_time"):
                    if session_calendar.is_session_time(cursor):
                        ordered_starts.append(cursor)
                elif hasattr(session_calendar, "contains"):
                    if session_calendar.contains(cursor):
                        ordered_starts.append(cursor)
                elif callable(session_calendar) and session_calendar(cursor):
                    ordered_starts.append(cursor)
                cursor += interval

    bars: list[Bar] = []
    carry_close: Decimal | None = None
    replay_end = end.astimezone(UTC) if end is not None else None
    for bucket_start in ordered_starts:
        bucket_trades = sorted(grouped.get(bucket_start, []), key=lambda item: item.event_time)
        if bucket_trades:
            prices = [trade.price for trade in bucket_trades]
            bar_open, bar_high, bar_low, bar_close = prices[0], max(prices), min(prices), prices[-1]
            bar_volume = sum((trade.quantity for trade in bucket_trades), Decimal(0))
            trade_count = len(bucket_trades)
            carry_close = bar_close
        else:
            empty = carry_close or Decimal(0)
            bar_open = bar_high = bar_low = bar_close = empty
            bar_volume = Decimal(0)
            trade_count = 0
        bar_end = bucket_start + interval
        final = replay_end is None or bar_end <= replay_end
        bars.append(Bar(bucket_start, bar_end, bar_open, bar_high, bar_low, bar_close,
                        bar_volume, trade_count, final=final,
                        partial=replay_end is not None and bar_end > replay_end))
    return bars
\n\n
def aggregate_bars(
    bars: list[Bar],
    interval_seconds: int,
    source_interval_seconds: int,
    *,
    timestamp: datetime | None = None,
) -> list[Bar]:
    """Aggregate only complete, contiguous source bars into higher-timeframe bars.

    Buckets are UTC epoch-aligned. A bucket is emitted only when every expected source
    interval is present, final, non-partial, and fully closed by the optional timestamp.
    This deliberately fails closed across missing bars and session gaps.
    """
    if interval_seconds <= 0 or source_interval_seconds <= 0:
        raise ValueError("bar intervals must be positive")
    if interval_seconds % source_interval_seconds:
        raise ValueError("target interval must be divisible by source interval")
    expected = interval_seconds // source_interval_seconds
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    grouped: dict[datetime, list[Bar]] = {}
    for bar in bars:
        if not bar.final or bar.partial:
            continue
        if int((bar.bar_end - bar.bar_start).total_seconds()) != source_interval_seconds:
            continue
        start = bar.bar_start.astimezone(UTC)
        elapsed = start - epoch
        seconds = elapsed.days * 86400 + elapsed.seconds
        bucket = epoch + timedelta(seconds=(seconds // interval_seconds) * interval_seconds)
        bucket_end = bucket + timedelta(seconds=interval_seconds)
        if bar.bar_start < bucket or bar.bar_end > bucket_end:
            continue
        if timestamp is not None and bar.bar_end > timestamp:
            continue
        grouped.setdefault(bucket, []).append(bar)

    result: list[Bar] = []
    for bucket, members in sorted(grouped.items()):
        members.sort(key=lambda item: item.bar_start)
        if len(members) != expected:
            continue
        if any(
            members[i].bar_start != bucket + timedelta(seconds=i * source_interval_seconds)
            or members[i].bar_end != bucket + timedelta(seconds=(i + 1) * source_interval_seconds)
            for i in range(expected)
        ):
            continue
        result.append(Bar(
            bucket,
            bucket + timedelta(seconds=interval_seconds),
            members[0].open,
            max(item.high for item in members),
            min(item.low for item in members),
            members[-1].close,
            sum((item.volume for item in members), Decimal(0)),
            sum(item.trade_count for item in members),
            final=True,
            partial=False,
        ))
    return result


class SessionCalendar:
    """UTC-preserving session calendar. Ranges remain timezone-aware UTC values."""

    def __init__(self, sessions: list[tuple[datetime, datetime]] | None = None) -> None:
        self.sessions = [(a.astimezone(UTC), b.astimezone(UTC)) for a, b in (sessions or [])]

    def is_session_time(self, timestamp: datetime) -> bool:
        timestamp = timestamp.astimezone(UTC)
        return any(start <= timestamp < end for start, end in self.sessions)

    contains = is_session_time


# Explicit 30-minute rollup alias for production replay callers.
def rollup_30m(trades: list[Trade], **kwargs: object) -> list[Bar]:
    return rollup(trades, 30 * 60, **kwargs)
