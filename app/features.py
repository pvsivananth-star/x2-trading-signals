"""Causal technical features for replay and live use."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.backtest.bars import Bar


@dataclass(frozen=True, slots=True)
class FeatureSnapshot:
    timestamp: datetime
    vwap: Decimal | None
    atr: Decimal | None
    rvol: Decimal | None
    swing_high: Decimal | None
    swing_low: Decimal | None
    fib_382: Decimal | None
    fib_500: Decimal | None
    fib_618: Decimal | None
    trend: str
    structure: str
    move_room: Decimal | None


def bars_through(bars: list[Bar], timestamp: datetime) -> list[Bar]:
    """Return bars whose complete end time is at or before t; never a later bar."""
    return [bar for bar in bars if bar.bar_end <= timestamp]


def causal_vwap(bars: list[Bar], timestamp: datetime) -> Decimal | None:
    eligible = bars_through(bars, timestamp)
    if not eligible:
        return None
    volume = sum((bar.volume for bar in eligible), Decimal(0))
    if volume <= 0:
        return None
    notional = sum((bar.close * bar.volume for bar in eligible), Decimal(0))
    return notional / volume


def causal_atr(bars: list[Bar], timestamp: datetime, period: int = 14) -> Decimal | None:
    eligible = bars_through(bars, timestamp)
    if period <= 0:
        raise ValueError("period must be positive")
    if len(eligible) < 2:
        return None
    true_ranges: list[Decimal] = []
    for index, bar in enumerate(eligible):
        if index == 0:
            tr = bar.high - bar.low
        else:
            previous_close = eligible[index - 1].close
            tr = max(bar.high - bar.low, abs(bar.high - previous_close), abs(bar.low - previous_close))
        true_ranges.append(tr)
    selected = true_ranges[-period:] if len(true_ranges) >= period else true_ranges
    return sum(selected, Decimal(0)) / Decimal(len(selected))


def causal_rvol(bars: list[Bar], timestamp: datetime, lookback: int = 20) -> Decimal | None:
    eligible = bars_through(bars, timestamp)
    if lookback <= 0:
        raise ValueError("lookback must be positive")
    if not eligible:
        return None
    current = eligible[-1].volume
    history = eligible[-lookback:-1]
    if not history:
        return Decimal(1) if current > 0 else None
    average = sum((bar.volume for bar in history), Decimal(0)) / Decimal(len(history))
    return None if average <= 0 else current / average


def causal_swing(bars: list[Bar], timestamp: datetime, period: int = 3) -> tuple[Decimal | None, Decimal | None]:
    eligible = bars_through(bars, timestamp)
    if period <= 0:
        raise ValueError("period must be positive")
    if len(eligible) < 2 * period + 1:
        return None, None
    recent = eligible[-(2 * period + 1):]
    middle = period
    if all(recent[middle].high >= bar.high for bar in recent[:middle] + recent[middle + 1:]):
        swing_high: Decimal | None = recent[middle].high
    else:
        swing_high = None
    if all(recent[middle].low <= bar.low for bar in recent[:middle] + recent[middle + 1:]):
        swing_low: Decimal | None = recent[middle].low
    else:
        swing_low = None
    return swing_high, swing_low


def fibonacci_zones(bars: list[Bar], timestamp: datetime, lookback: int = 20) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    eligible = bars_through(bars, timestamp)
    if lookback <= 0:
        raise ValueError("lookback must be positive")
    if not eligible:
        return None, None, None
    window = eligible[-lookback:] if len(eligible) >= lookback else eligible
    low = min(bar.low for bar in window)
    high = max(bar.high for bar in window)
    rng = high - low
    return low + rng * Decimal("0.382"), low + rng * Decimal("0.5"), low + rng * Decimal("0.618")


def causal_trend_structure(bars: list[Bar], timestamp: datetime, period: int = 5) -> tuple[str, str]:
    eligible = bars_through(bars, timestamp)
    if period <= 0:
        raise ValueError("period must be positive")
    if len(eligible) < period:
        return "unknown", "range"
    window = eligible[-period:]
    highs = [bar.high for bar in window]
    lows = [bar.low for bar in window]
    trend = "up" if window[-1].close > window[0].close else "down" if window[-1].close < window[0].close else "sideways"
    if all(highs[index] > highs[index - 1] and lows[index] > lows[index - 1] for index in range(1, len(window))):
        structure = "higher-highs-higher-lows"
    elif all(highs[index] < highs[index - 1] and lows[index] < lows[index - 1] for index in range(1, len(window))):
        structure = "lower-highs-lower-lows"
    else:
        structure = "mixed"
    return trend, structure


def net_move_room(bars: list[Bar], timestamp: datetime, target: Decimal, stop: Decimal) -> Decimal | None:
    eligible = bars_through(bars, timestamp)
    if not eligible:
        return None
    last = eligible[-1].close
    if last <= 0:
        return None
    upside = abs(target - last)
    downside = abs(last - stop)
    net_price_room = upside - downside
    return net_price_room / last


def feature_snapshot(bars: list[Bar], timestamp: datetime) -> FeatureSnapshot:
    swing_high, swing_low = causal_swing(bars, timestamp)
    fib_382, fib_500, fib_618 = fibonacci_zones(bars, timestamp)
    trend, structure = causal_trend_structure(bars, timestamp)
    return FeatureSnapshot(
        timestamp=timestamp,
        vwap=causal_vwap(bars, timestamp),
        atr=causal_atr(bars, timestamp),
        rvol=causal_rvol(bars, timestamp),
        swing_high=swing_high,
        swing_low=swing_low,
        fib_382=fib_382,
        fib_500=fib_500,
        fib_618=fib_618,
        trend=trend,
        structure=structure,
        move_room=net_move_room(bars, timestamp, fib_618 or Decimal(0), fib_382 or Decimal(0)) if fib_382 is not None and fib_618 is not None else None,
    )
