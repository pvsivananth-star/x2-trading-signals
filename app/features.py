"""Causal technical features for replay and live use."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.backtest.bars import Bar

DEFAULT_SESSION_TZ = "America/New_York"
DEFAULT_SESSION_OPEN = time(9, 30)
DEFAULT_SESSION_CLOSE = time(16, 0)


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
    structure_target_continuation: Decimal | None = None
    structure_source_continuation: str | None = None


def bars_through(bars: list[Bar], timestamp: datetime) -> list[Bar]:
    """Return bars whose complete end time is at or before t; never a later bar."""
    return [bar for bar in bars if bar.bar_end <= timestamp]


def _session_history(
    bars: list[Bar],
    timestamp: datetime,
    session_timezone: str,
    session_open: time | None,
    session_close: time | None,
) -> list[Bar]:
    zone = ZoneInfo(session_timezone)
    history = []
    for bar in bars_through(bars, timestamp):
        local_start = bar.bar_start.astimezone(zone)
        if session_open is not None and local_start.timetz().replace(tzinfo=None) < session_open:
            continue
        if session_close is not None and local_start.timetz().replace(tzinfo=None) >= session_close:
            continue
        history.append(bar)
    return history


def _session_bars(
    bars: list[Bar],
    timestamp: datetime,
    session_timezone: str = DEFAULT_SESSION_TZ,
    session_open: time | None = DEFAULT_SESSION_OPEN,
    session_close: time | None = DEFAULT_SESSION_CLOSE,
) -> list[Bar]:
    """Return only bars from the current exchange-local session."""
    zone = ZoneInfo(session_timezone)
    session_date = timestamp.astimezone(zone).date()
    return [
        bar for bar in _session_history(bars, timestamp, session_timezone, session_open, session_close)
        if bar.bar_start.astimezone(zone).date() == session_date
    ]


def causal_vwap(
    bars: list[Bar],
    timestamp: datetime,
    *,
    session_timezone: str = DEFAULT_SESSION_TZ,
    session_open: time | None = DEFAULT_SESSION_OPEN,
    session_close: time | None = DEFAULT_SESSION_CLOSE,
) -> Decimal | None:
    """Return the volume-weighted close for the current exchange-local session."""
    eligible = _session_bars(bars, timestamp, session_timezone, session_open, session_close)
    if not eligible:
        return None
    volume = sum((bar.volume for bar in eligible), Decimal(0))
    if volume <= 0:
        return None
    notional = sum((bar.close * bar.volume for bar in eligible), Decimal(0))
    return notional / volume


def causal_atr(
    bars: list[Bar],
    timestamp: datetime,
    period: int = 14,
    *,
    session_timezone: str = DEFAULT_SESSION_TZ,
    session_open: time | None = DEFAULT_SESSION_OPEN,
    session_close: time | None = DEFAULT_SESSION_CLOSE,
) -> Decimal | None:
    eligible = _session_bars(bars, timestamp, session_timezone, session_open, session_close)
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


def causal_swing(
    bars: list[Bar],
    timestamp: datetime,
    period: int = 3,
    *,
    session_timezone: str = DEFAULT_SESSION_TZ,
    session_open: time | None = DEFAULT_SESSION_OPEN,
    session_close: time | None = DEFAULT_SESSION_CLOSE,
) -> tuple[Decimal | None, Decimal | None]:
    eligible = _session_bars(bars, timestamp, session_timezone, session_open, session_close)
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


def fibonacci_zones(
    bars: list[Bar],
    timestamp: datetime,
    lookback: int = 20,
    *,
    session_timezone: str = DEFAULT_SESSION_TZ,
    session_open: time | None = DEFAULT_SESSION_OPEN,
    session_close: time | None = DEFAULT_SESSION_CLOSE,
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    eligible = _session_bars(bars, timestamp, session_timezone, session_open, session_close)
    if lookback <= 0:
        raise ValueError("lookback must be positive")
    if not eligible:
        return None, None, None
    window = eligible[-lookback:] if len(eligible) >= lookback else eligible
    low = min(bar.low for bar in window)
    high = max(bar.high for bar in window)
    rng = high - low
    return low + rng * Decimal("0.382"), low + rng * Decimal("0.5"), low + rng * Decimal("0.618")


def causal_trend_structure(
    bars: list[Bar],
    timestamp: datetime,
    period: int = 24,
    *,
    session_timezone: str = DEFAULT_SESSION_TZ,
    session_open: time | None = DEFAULT_SESSION_OPEN,
    session_close: time | None = DEFAULT_SESSION_CLOSE,
) -> tuple[str, str]:
    if period <= 0:
        raise ValueError("period must be positive")
    eligible = _session_bars(bars, timestamp, session_timezone, session_open, session_close)
    if len(eligible) < 6:
        return "unknown", "range"
    window = eligible[-min(len(eligible), period):]
    atr = causal_atr(
        bars, timestamp, session_timezone=session_timezone,
        session_open=session_open, session_close=session_close,
    )
    if atr is None or abs(window[-1].close - window[0].close) < Decimal("1.25") * atr:
        return "sideways", "range"
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


def causal_pullback_depth(
    bars: list[Bar],
    timestamp: datetime,
    lookback: int = 20,
    *,
    session_timezone: str = DEFAULT_SESSION_TZ,
    session_open: time | None = DEFAULT_SESSION_OPEN,
    session_close: time | None = DEFAULT_SESSION_CLOSE,
) -> tuple[Decimal | None, Decimal | None]:
    """Return (last_close, retracement) where retracement = (high - close) / (high - low) over the window."""
    eligible = _session_bars(bars, timestamp, session_timezone, session_open, session_close)
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


def causal_structure_target(
    bars: list[Bar],
    timestamp: datetime,
    *,
    include_session_high: bool = True,
    session_timezone: str = DEFAULT_SESSION_TZ,
    session_open: time | None = DEFAULT_SESSION_OPEN,
    session_close: time | None = DEFAULT_SESSION_CLOSE,
) -> tuple[Decimal | None, str | None]:
    """Nearest structural resistance strictly above the last close (long targets).

    Candidates: confirmed swing high, prior session high, current session high so far.
    Historical candidates are grouped by the configured exchange-local session date.
    """
    eligible = _session_history(bars, timestamp, session_timezone, session_open, session_close)
    if not eligible:
        return None, None
    last = eligible[-1]
    close = last.close
    candidates: list[tuple[str, Decimal]] = []
    swing_high, _swing_low = causal_swing(
        bars, timestamp, session_timezone=session_timezone,
        session_open=session_open, session_close=session_close,
    )
    if swing_high is not None:
        candidates.append(("SWING_HIGH", swing_high))
    zone = ZoneInfo(session_timezone)
    today = timestamp.astimezone(zone).date()
    prior = [b for b in eligible if b.bar_start.astimezone(zone).date() < today]
    if prior:
        prev_day = max(b.bar_start.astimezone(zone).date() for b in prior)
        candidates.append(("PRIOR_SESSION_HIGH", max(
            b.high for b in prior if b.bar_start.astimezone(zone).date() == prev_day
        )))
    current = [b for b in eligible if b.bar_start.astimezone(zone).date() == today]
    if include_session_high and current:
        candidates.append(("SESSION_HIGH", max(b.high for b in current)))
    above = [(src, lvl) for src, lvl in candidates if lvl > close]
    if not above:
        return None, None
    src, lvl = min(above, key=lambda item: item[1])
    return lvl, src


def causal_pullback_depth_short(
    bars: list[Bar],
    timestamp: datetime,
    lookback: int = 20,
    *,
    session_timezone: str = DEFAULT_SESSION_TZ,
    session_open: time | None = DEFAULT_SESSION_OPEN,
    session_close: time | None = DEFAULT_SESSION_CLOSE,
) -> Decimal | None:
    """Bounce depth for a short: (close - window low) / (window high - window low)."""
    eligible = _session_bars(bars, timestamp, session_timezone, session_open, session_close)
    if lookback <= 0:
        raise ValueError("lookback must be positive")
    if not eligible:
        return None
    window = eligible[-lookback:]
    rng = max(bar.high for bar in window) - min(bar.low for bar in window)
    if rng <= 0:
        return None
    return (eligible[-1].close - min(bar.low for bar in window)) / rng


def causal_structure_target_short(
    bars: list[Bar],
    timestamp: datetime,
    *,
    session_timezone: str = DEFAULT_SESSION_TZ,
    session_open: time | None = DEFAULT_SESSION_OPEN,
    session_close: time | None = DEFAULT_SESSION_CLOSE,
) -> tuple[Decimal | None, str | None]:
    """Nearest structural support strictly below the last close (short targets).

    Candidates: confirmed swing low, prior session low, current session low so far.
    Historical candidates are grouped by the configured exchange-local session date.
    """
    eligible = _session_history(bars, timestamp, session_timezone, session_open, session_close)
    if not eligible:
        return None, None
    last = eligible[-1]
    close = last.close
    candidates: list[tuple[str, Decimal]] = []
    _swing_high, swing_low = causal_swing(
        bars, timestamp, session_timezone=session_timezone,
        session_open=session_open, session_close=session_close,
    )
    if swing_low is not None:
        candidates.append(("SWING_LOW", swing_low))
    zone = ZoneInfo(session_timezone)
    today = timestamp.astimezone(zone).date()
    prior = [b for b in eligible if b.bar_start.astimezone(zone).date() < today]
    if prior:
        prev_day = max(b.bar_start.astimezone(zone).date() for b in prior)
        candidates.append(("PRIOR_SESSION_LOW", min(
            b.low for b in prior if b.bar_start.astimezone(zone).date() == prev_day
        )))
    current = [b for b in eligible if b.bar_start.astimezone(zone).date() == today]
    if current:
        candidates.append(("SESSION_LOW", min(b.low for b in current)))
    below = [(src, lvl) for src, lvl in candidates if lvl < close]
    if not below:
        return None, None
    src, lvl = max(below, key=lambda item: item[1])
    return lvl, src


def feature_snapshot(
    bars: list[Bar],
    timestamp: datetime,
    *,
    session_timezone: str = DEFAULT_SESSION_TZ,
    session_open: time | None = DEFAULT_SESSION_OPEN,
    session_close: time | None = DEFAULT_SESSION_CLOSE,
) -> FeatureSnapshot:
    session = {
        "session_timezone": session_timezone,
        "session_open": session_open,
        "session_close": session_close,
    }
    swing_high, swing_low = causal_swing(bars, timestamp, **session)
    structure_target, structure_source = causal_structure_target(bars, timestamp, **session)
    continuation_target, continuation_source = causal_structure_target(
        bars, timestamp, include_session_high=False, **session
    )
    structure_target_short, structure_source_short = causal_structure_target_short(bars, timestamp, **session)
    fib_382, fib_500, fib_618 = fibonacci_zones(bars, timestamp, **session)
    trend, structure = causal_trend_structure(bars, timestamp, **session)
    last_close, pullback_depth = causal_pullback_depth(bars, timestamp, **session)
    return FeatureSnapshot(
        timestamp=timestamp,
        vwap=causal_vwap(bars, timestamp, **session),
        atr=causal_atr(bars, timestamp, **session),
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
        pullback_depth_short=causal_pullback_depth_short(bars, timestamp, **session),
        structure_target_short=structure_target_short,
        structure_source_short=structure_source_short,
        structure_target_continuation=continuation_target,
        structure_source_continuation=continuation_source,
    )
