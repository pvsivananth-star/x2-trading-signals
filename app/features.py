"""Causal technical features for replay and live use."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
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
    last_close: Decimal | None = None
    pullback_depth: Decimal | None = None
    structure_target: Decimal | None = None
    structure_source: str | None = None
    pullback_depth_short: Decimal | None = None
    structure_target_short: Decimal | None = None
    structure_source_short: str | None = None


def bars_through(bars: list[Bar], timestamp: datetime) -> list[Bar]:
    """Return bars whose complete end time is at or before t; never a later bar."""
    return [bar for bar in bars if bar.bar_end <= timestamp]


def causal_vwap(bars: list[Bar], timestamp: datetime) -> Decimal | None:
    """Return the volume-weighted close since the latest UTC day boundary."""
    eligible = bars_through(bars, timestamp)
    if not eligible:
        return None
    session_date = eligible[-1].bar_start.astimezone(UTC).date()
    eligible = [bar for bar in eligible if bar.bar_start.astimezone(UTC).date() == session_date]
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


def causal_pullback_depth(bars: list[Bar], timestamp: datetime, lookback: int = 20) -> tuple[Decimal | None, Decimal | None]:
    """Return (last_close, retracement) where retracement = (high - close) / (high - low) over the window."""
    eligible = bars_through(bars, timestamp)
    if lookback <= 0:
        raise ValueError("lookback must be positive")
    if not eligible:
        return None, None
    window = eligible[-lookback:]
    last_close = eligible[-1].close
    rng = max(bar.high for bar in window) - min(bar.low for bar in window)
    if rng <= 0:
        return last_close, None
    depth = (max(bar.high for bar in window) - last_close) / rng
    return last_close, depth


def causal_structure_target(bars: list[Bar], timestamp: datetime) -> tuple[Decimal | None, str | None]:
    """Nearest structural resistance strictly above the last close (long targets).

    Candidates: confirmed swing high, prior session high, current session high so far.
    Sessions are grouped by UTC date of bar_start (approximation; exchange-local sessions
    for non-US markets are not yet modeled).
    """
    eligible = bars_through(bars, timestamp)
    if not eligible:
        return None, None
    last = eligible[-1]
    close = last.close
    candidates: list[tuple[str, Decimal]] = []
    swing_high, _swing_low = causal_swing(bars, timestamp)
    if swing_high is not None:
        candidates.append(("SWING_HIGH", swing_high))
    today = last.bar_start.date()
    prior = [b for b in eligible if b.bar_start.date() < today]
    if prior:
        prev_day = max(b.bar_start.date() for b in prior)
        candidates.append(("PRIOR_SESSION_HIGH", max(b.high for b in prior if b.bar_start.date() == prev_day)))
    current = [b for b in eligible if b.bar_start.date() == today]
    candidates.append(("SESSION_HIGH", max(b.high for b in current)))
    above = [(src, lvl) for src, lvl in candidates if lvl > close]
    if not above:
        return None, None
    src, lvl = min(above, key=lambda item: item[1])
    return lvl, src


def causal_pullback_depth_short(bars: list[Bar], timestamp: datetime, lookback: int = 20) -> Decimal | None:
    """Bounce depth for a short: (close - window low) / (window high - window low)."""
    eligible = bars_through(bars, timestamp)
    if lookback <= 0:
        raise ValueError("lookback must be positive")
    if not eligible:
        return None
    window = eligible[-lookback:]
    rng = max(bar.high for bar in window) - min(bar.low for bar in window)
    if rng <= 0:
        return None
    return (eligible[-1].close - min(bar.low for bar in window)) / rng


def causal_structure_target_short(bars: list[Bar], timestamp: datetime) -> tuple[Decimal | None, str | None]:
    """Nearest structural support strictly below the last close (short targets).

    Candidates: confirmed swing low, prior session low, current session low so far.
    Sessions are UTC-date approximations, as for the long target.
    """
    eligible = bars_through(bars, timestamp)
    if not eligible:
        return None, None
    last = eligible[-1]
    close = last.close
    candidates: list[tuple[str, Decimal]] = []
    _swing_high, swing_low = causal_swing(bars, timestamp)
    if swing_low is not None:
        candidates.append(("SWING_LOW", swing_low))
    today = last.bar_start.date()
    prior = [b for b in eligible if b.bar_start.date() < today]
    if prior:
        prev_day = max(b.bar_start.date() for b in prior)
        candidates.append(("PRIOR_SESSION_LOW", min(b.low for b in prior if b.bar_start.date() == prev_day)))
    current = [b for b in eligible if b.bar_start.date() == today]
    candidates.append(("SESSION_LOW", min(b.low for b in current)))
    below = [(src, lvl) for src, lvl in candidates if lvl < close]
    if not below:
        return None, None
    src, lvl = max(below, key=lambda item: item[1])
    return lvl, src


def feature_snapshot(bars: list[Bar], timestamp: datetime) -> FeatureSnapshot:
    swing_high, swing_low = causal_swing(bars, timestamp)
    structure_target, structure_source = causal_structure_target(bars, timestamp)
    structure_target_short, structure_source_short = causal_structure_target_short(bars, timestamp)
    fib_382, fib_500, fib_618 = fibonacci_zones(bars, timestamp)
    trend, structure = causal_trend_structure(bars, timestamp)
    last_close, pullback_depth = causal_pullback_depth(bars, timestamp)
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
        last_close=last_close,
        pullback_depth=pullback_depth,
        structure_target=structure_target,
        structure_source=structure_source,
        pullback_depth_short=causal_pullback_depth_short(bars, timestamp),
        structure_target_short=structure_target_short,
        structure_source_short=structure_source_short,
    )
