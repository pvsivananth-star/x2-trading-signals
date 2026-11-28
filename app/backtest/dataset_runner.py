"""Run the Confluence_v1 strategy over a local bars dataset (synthetic or licensed-local).

Execution model (conservative, documented):
- Signals are evaluated on the close of each bar using only bars that have closed.
- Entries fill at the OPEN of the next bar, with a stop placed 1 ATR from the fill and the
  structural target from the signal card.
- Stops and targets are checked on the fill bar and every later bar; stop wins ambiguous bars.
- Gap-through stops fill at the bar open. A hard holding-time exit is measured from the fill.
- A configurable stagnation exit closes positions that fail to reach a minimum favourable R.
- Strategy exits (trend break, mandatory pre-close flat) fill at the close of the bar that
  produced them.
- Each symbol trades its own equity allocation; results are reported per symbol and combined.

Costs use config/costs.yaml. Fields that are not verified are excluded and listed in the
summary; estimates are applied only when allow_estimates is enabled.
"""

from __future__ import annotations

import csv
import json
import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.backtest.bars import Bar, aggregate_bars
from app.backtest.portfolio import PortfolioLedger
from app.backtest.simulator import OrderSimulator, SimulationConfig
from app.costs import CostBlockedError, _field, load_costs
from app.event_risk import EventRiskAssessment
from app.features import (
    DEFAULT_SESSION_CLOSE,
    DEFAULT_SESSION_OPEN,
    DEFAULT_SESSION_TZ,
    FeatureSnapshot,
    _local_dt,
    _session_context,
    _zone,
    causal_atr,
    causal_confirmed_swing,
    feature_snapshot,
)
from app.policy import TradingPolicy
from app.real_data import group_bars, load_bar_records
from app.regime import Regime
from app.strategy import ConfluenceV1

# Synthetic and multi-asset instruments inherit the threshold profile of their configured market.
DEFAULT_PROFILE = {
    "SIM-EQ": "US_EQUITIES",
    "SIM-COIN": "CRYPTO_MAJORS",
    "SPY": "US_EQUITIES",
    "NVDA": "US_EQUITIES",
    "GLD": "US_COMMODITIES",
    "USO": "US_COMMODITIES",
    "BTC-USD": "CRYPTO_MAJORS",
    "ETH-USD": "CRYPTO_MAJORS",
    "NIFTYBEES.NS": "INDIA_NSE",
    "RELIANCE.NS": "INDIA_NSE",
    "XIU.TO": "CANADA_TSX",
    "ISF.L": "UK_LSE",
    "SAP.DE": "GERMANY_XETRA",
    "ASML.AS": "NETHERLANDS_AEX",
    "1306.T": "JAPAN_TSE",
    "D05.SI": "SINGAPORE_SGX",
    "BHP.AX": "AUSTRALIA_ASX",
    "005930.KS": "KOREA_KRX",
}

_US_EQUITY_COST_FIELDS = (
    "commission_per_share", "commission_min_per_order", "commission_max_pct_of_trade",
    "sec_fee_sell_rate", "finra_taf_sell_per_share", "finra_cat_per_share",
    "clearing_nscc_dtc_per_share", "spread_pct_full", "slippage_pct_per_side",
    "exchange_fee_per_share",
)

COST_SECTION = {
    "US_EQUITIES": "US_EQUITIES",
    "US_COMMODITIES": "US_ETF",
    "CRYPTO_MAJORS": "CRYPTO_BTC_SPOT",
    "INDIA_NSE": "INDIA_NSE",
    "CANADA_TSX": "CANADA_TSX",
    "UK_LSE": "UK_LSE",
    "GERMANY_XETRA": "GERMANY_XETRA",
    "NETHERLANDS_AEX": "NETHERLANDS_AEX",
    "FRANCE_EURONEXT": "FRANCE_EURONEXT",
    "SWITZERLAND_SIX": "SWITZERLAND_SIX",
    "ITALY_BIT": "ITALY_BIT",
    "SPAIN_BME": "SPAIN_BME",
    "SWEDEN_OMX": "SWEDEN_OMX",
    "JAPAN_TSE": "JAPAN_TSE",
    "SINGAPORE_SGX": "SINGAPORE_SGX",
    "AUSTRALIA_ASX": "AUSTRALIA_ASX",
    "KOREA_KRX": "KOREA_KRX",
}

COST_FIELDS: dict[str, tuple[str, ...]] = {
    "US_EQUITIES": _US_EQUITY_COST_FIELDS,
    "US_COMMODITIES": _US_EQUITY_COST_FIELDS,
    "CRYPTO_MAJORS": ("commission", "spread_pct_full"),
}

_EQUITY_COST_PROFILES = {"US_EQUITIES", "US_COMMODITIES"}
_FRACTIONAL_QTY_PROFILES = {"CRYPTO_MAJORS"}

MARKET_SESSIONS: dict[str, tuple[str, time | None, time | None]] = {
    "US_EQUITIES": (DEFAULT_SESSION_TZ, DEFAULT_SESSION_OPEN, DEFAULT_SESSION_CLOSE),
    "US_COMMODITIES": (DEFAULT_SESSION_TZ, DEFAULT_SESSION_OPEN, DEFAULT_SESSION_CLOSE),
    "CRYPTO_MAJORS": ("UTC", None, None),
    "INDIA_NSE": ("Asia/Kolkata", time(9, 15), time(15, 30)),
    "CANADA_TSX": ("America/Toronto", time(9, 30), time(16, 0)),
    "UK_LSE": ("Europe/London", time(8, 0), time(16, 30)),
    "GERMANY_XETRA": ("Europe/Berlin", time(9, 0), time(17, 30)),
    "NETHERLANDS_AEX": ("Europe/Amsterdam", time(9, 0), time(17, 30)),
    "FRANCE_EURONEXT": ("Europe/Paris", time(9, 0), time(17, 30)),
    "SWITZERLAND_SIX": ("Europe/Zurich", time(9, 0), time(17, 30)),
    "ITALY_BIT": ("Europe/Rome", time(9, 0), time(17, 30)),
    "SPAIN_BME": ("Europe/Madrid", time(9, 0), time(17, 30)),
    "SWEDEN_OMX": ("Europe/Stockholm", time(9, 0), time(17, 30)),
    "JAPAN_TSE": ("Asia/Tokyo", time(9, 0), time(15, 0)),
    "SINGAPORE_SGX": ("Asia/Singapore", time(9, 0), time(17, 0)),
    "AUSTRALIA_ASX": ("Australia/Sydney", time(10, 0), time(16, 0)),
    "KOREA_KRX": ("Asia/Seoul", time(9, 0), time(15, 30)),
}


def causal_regime(
        bars: list[Bar],
        timestamp: datetime,
        *,
        session_timezone: str = DEFAULT_SESSION_TZ,
        session_open: time | None = DEFAULT_SESSION_OPEN,
        session_close: time | None = DEFAULT_SESSION_CLOSE,
) -> Regime:
    """Classify the current session after six bars using session or rolling efficiency."""
    context = _session_context(bars, timestamp, session_timezone, session_open, session_close)
    eligible = context.current
    if len(eligible) < 6:
        return Regime.CHOP_DAY
    atr = causal_atr(
        bars,
        timestamp,
        session_timezone=session_timezone,
        session_open=session_open,
        session_close=session_close,
        _context=context,
    )
    if atr is None:
        return Regime.CHOP_DAY
    window = eligible[-min(len(eligible), 6):]
    window_disp = abs(window[-1].close - window[0].open)
    window_range = max(bar.high for bar in window) - min(bar.low for bar in window)
    window_eff = (window_disp / window_range) if window_range > 0 else Decimal(0)
    session_disp = abs(eligible[-1].close - eligible[0].open)
    session_range = max(bar.high for bar in eligible) - min(bar.low for bar in eligible)
    is_trend = (
            session_disp >= Decimal("1.35") * atr
            or (window_disp >= Decimal("1.35") * atr and window_eff >= Decimal("0.45"))
            or (
                session_range >= Decimal("2.25") * atr
                and window_eff >= Decimal("0.45")
                and window_disp > 0
            )
    )
    return Regime.TREND_DAY if is_trend else Regime.CHOP_DAY

@dataclass
class Trade:
    symbol: str
    side: str
    qty: Decimal
    entry_time: datetime
    entry_price: Decimal
    exit_time: datetime
    exit_price: Decimal
    exit_reason: str
    risk_amount: Decimal
    gross_pnl: Decimal
    cost: Decimal | None
    net_pnl: Decimal | None
    r_multiple: Decimal | None
    holding_minutes: Decimal = Decimal(0)
    regime: str = "UNKNOWN"
    fill_status: str = "filled"
    cost_status: str = "gross"
    portfolio_status: str = "unprocessed"
    market_phase: str = "UNKNOWN"


@dataclass
class Position:
    side: int
    qty: Decimal
    entry_time: datetime
    entry_price: Decimal
    stop: Decimal
    target: Decimal
    risk_amount: Decimal
    entry_cost: Decimal | None
    best_favorable_r: Decimal = Decimal(0)
    entry_regime: str = "UNKNOWN"
    entry_fill_status: str = "filled"
    cost_status: str = "gross"
    entry_market_phase: str = "UNKNOWN"



def classify_market_phase(
    regime: Regime,
    snapshot: FeatureSnapshot,
    prior_snapshots: list[FeatureSnapshot],
) -> str:
    """Create a reporting bucket; it does not alter strategy entry eligibility.

    Trend direction separates bull/bear trend days. Within non-trend sessions, a 5m ATR
    at least 1.5x the mean of up to 20 earlier ATR observations is high-volatility/choppy.
    Remaining non-trend sessions are split into sideways and normal directional conditions.
    """
    if regime is Regime.TREND_DAY:
        if snapshot.trend == "up":
            return "BULL_TREND"
        if snapshot.trend == "down":
            return "BEAR_TREND"
        return "NORMAL"
    if regime is Regime.CHOP_DAY:
        prior_atrs = [item.atr for item in prior_snapshots[-20:] if item.atr is not None]
        if snapshot.atr is not None and len(prior_atrs) >= 5:
            baseline = sum(prior_atrs, Decimal(0)) / Decimal(len(prior_atrs))
            if baseline > 0 and snapshot.atr >= baseline * Decimal("1.5"):
                return "HIGH_VOL_CHOP"
        return "SIDEWAYS" if snapshot.trend == "sideways" else "NORMAL"
    return "UNKNOWN"


def minutes_to_close(bar_end: datetime, profile: str) -> int | None:
    """Minutes from the bar end to the regular close. None for 24x7 markets (no session flat)."""
    timezone, _session_open, session_close = MARKET_SESSIONS.get(
        profile, (DEFAULT_SESSION_TZ, DEFAULT_SESSION_OPEN, DEFAULT_SESSION_CLOSE)
    )
    if session_close is None:
        return None
    local_end = _local_dt(bar_end, timezone)
    close = datetime.combine(local_end.date(), session_close, tzinfo=_zone(timezone))
    return int((close - local_end).total_seconds() // 60)


def _cost_model(costs: dict[str, Any], profile: str, allow_estimates: bool, symbol: str | None = None) -> tuple[dict[str, Any], list[str]]:
    section = COST_SECTION.get(profile, profile)
    estimates_enabled = allow_estimates or costs.get("allow_estimates") is True
    field_costs = {**costs, "allow_estimates": estimates_enabled}
    excluded: list[str] = []
    values: dict[str, Any] = {}
    fields = list(COST_FIELDS.get(profile, ("commission", "spread_pct_full")))
    spread_field = "spread_pct_full"
    if symbol and profile == "US_EQUITIES" and symbol != "SPY":
        spread_field = "spread_pct_full_single_stocks"
    elif symbol and profile == "US_COMMODITIES":
        spread_field = f"spread_pct_full_{symbol.lower()}"
    if "spread_pct_full" in fields:
        fields[fields.index("spread_pct_full")] = spread_field
    estimated: list[str] = []
    for name in fields:
        try:
            values[name] = _field(field_costs, section, name)
            current_section = section
            visited: set[str] = set()
            while current_section not in visited:
                visited.add(current_section)
                entry = costs.get(current_section, {})
                if name in entry:
                    if entry[name].get("status") == "estimate":
                        estimated.append(name)
                    break
                parent = entry.get("inherits")
                if not isinstance(parent, str):
                    break
                current_section = parent
        except CostBlockedError:
            values[name] = Decimal(0)
            excluded.append(name)
    if spread_field != "spread_pct_full":
        values["spread_pct_full"] = values.pop(spread_field)
    values["_excluded"] = excluded
    values["_estimated"] = estimated
    values["_slippage_on"] = "slippage_pct_per_side" not in excluded
    return values, excluded


def _commission(v: dict[str, Any], qty: Decimal, notional: Decimal) -> Decimal:
    per_side = max(v["commission_min_per_order"], v["commission_per_share"] * qty)
    return min(per_side, v["commission_max_pct_of_trade"] * notional)


def entry_costs(v: dict[str, Any], qty: Decimal, price: Decimal) -> Decimal:
    notional = qty * price
    cost = _commission(v, qty, notional) + v["finra_cat_per_share"] * qty + v["clearing_nscc_dtc_per_share"] * qty
    cost += v["spread_pct_full"] * notional
    if v["_slippage_on"]:
        cost += v["slippage_pct_per_side"] * notional
    cost += v["exchange_fee_per_share"] * qty
    return cost


def exit_costs(v: dict[str, Any], qty: Decimal, price: Decimal) -> Decimal:
    notional = qty * price
    cost = _commission(v, qty, notional) + v["finra_cat_per_share"] * qty + v["clearing_nscc_dtc_per_share"] * qty
    cost += v["sec_fee_sell_rate"] * notional + v["finra_taf_sell_per_share"] * qty
    if v["_slippage_on"]:
        cost += v["slippage_pct_per_side"] * notional
    cost += v["exchange_fee_per_share"] * qty
    return cost


def _intrabar_exit(pos: Position, bar: Bar) -> tuple[Decimal, str] | None:
    """Resolve OHLC exits conservatively and label bars where path ordering is ambiguous."""
    if pos.side > 0:
        stop_gap = bar.open <= pos.stop
        stop_touch = bar.low <= pos.stop
        target_touch = bar.high >= pos.target
    else:
        stop_gap = bar.open >= pos.stop
        stop_touch = bar.high >= pos.stop
        target_touch = bar.low <= pos.target
    if stop_gap:
        return bar.open, "HARD_STOP_GAP_BOTH_TOUCHED" if target_touch else "HARD_STOP_GAP"
    if stop_touch:
        return pos.stop, "HARD_STOP_BOTH_TOUCHED" if target_touch else "HARD_STOP"
    if target_touch:
        return pos.target, "TARGET_HIT"
    return None


def run_symbol(symbol: str, bars: list[Bar], profile: str, engine: ConfluenceV1, policy: TradingPolicy,
               costs: dict[str, Any], start_equity: Decimal, risk_pct: Decimal,
               allow_estimates: bool, order_simulator: OrderSimulator | None = None,
               event_risk_provider: Callable[[datetime, str], EventRiskAssessment | None] | None = None) -> dict[str, Any]:
    cost_values, excluded = _cost_model(costs, profile, allow_estimates, symbol)
    execution_cfg = getattr(engine, "strategy", {}).get("execution", {})
    simulator = order_simulator or OrderSimulator(SimulationConfig(
        partial_fill_ratio=Decimal(str(execution_cfg.get("partial_fill_ratio", "1")))
    ))
    partial_fills = 0
    rejected_entries = 0
    costs_applicable = profile in _EQUITY_COST_PROFILES
    net_available = costs_applicable and not excluded and not cost_values.get("_estimated")
    coverage_status = "fully_net" if net_available else "partial_net" if costs_applicable else "gross"
    session_timezone, session_open, session_close = MARKET_SESSIONS.get(
        profile, (DEFAULT_SESSION_TZ, DEFAULT_SESSION_OPEN, DEFAULT_SESSION_CLOSE)
    )
    session = {
        "session_timezone": session_timezone,
        "session_open": session_open,
        "session_close": session_close,
    }
    source_interval_seconds = int((bars[0].bar_end - bars[0].bar_start).total_seconds()) if bars else 0
    bars_15m = aggregate_bars(bars, 15 * 60, source_interval_seconds) if source_interval_seconds else []
    bars_30m = aggregate_bars(bars, 30 * 60, source_interval_seconds) if source_interval_seconds else []
    swing_cfg = getattr(engine, "strategy", {}).get("swing", {})
    volume_cfg = getattr(engine, "strategy", {}).get("volume", {})
    slot_lookback = int(volume_cfg.get("slot_rvol_lookback_sessions", 10))
    slot_min_sessions = int(volume_cfg.get("slot_rvol_min_sessions", 5))
    snaps: list[FeatureSnapshot] = []
    for i, bar in enumerate(bars):
        base = feature_snapshot(
            bars[: i + 1], bar.bar_end, **session,
            slot_rvol_lookback_sessions=slot_lookback,
            slot_rvol_min_sessions=slot_min_sessions,
        )
        complete_15m = [item for item in bars_15m if item.bar_end <= bar.bar_end]
        complete_30m = [item for item in bars_30m if item.bar_end <= bar.bar_end]
        context_time = complete_30m[-1].bar_end if complete_30m else None
        context_regime = (
            causal_regime(complete_30m, bar.bar_end, **session)
            if len(complete_30m) >= 6 else None
        )
        atr_15m = causal_atr(complete_15m, bar.bar_end, **session) if complete_15m else None
        swing_high, swing_low, swing_direction, swing_size_atr, swing_confirmed = causal_confirmed_swing(
            complete_15m,
            bar.bar_end,
            atr_15m,
            left_bars=int(swing_cfg.get("min_candles", 3)),
            confirmation_bars=int(swing_cfg.get("confirmation_bars", 2)),
            min_atr_mult=Decimal(str(swing_cfg.get("min_atr_mult", "0.60"))),
        )
        snaps.append(replace(
            base,
            context_regime=context_regime.value if context_regime is not None else None,
            context_timestamp=context_time,
            swing_15m_high=swing_high,
            swing_15m_low=swing_low,
            swing_15m_direction=swing_direction,
            swing_15m_size_atr=swing_size_atr,
            swing_15m_confirmed=swing_confirmed,
        ))
    trades: list[Trade] = []
    pos: Position | None = None
    pending: tuple[int, Decimal, Decimal, Decimal, str, Decimal, str, str] | None = None
    exit_cfg = getattr(engine, "strategy", {}).get("exit", {})
    max_holding_minutes = int(exit_cfg.get("max_holding_minutes", 240))
    stagnation_minutes = int(exit_cfg.get("stagnation_minutes", 60))
    stagnation_min_favorable_r = Decimal(str(exit_cfg.get("stagnation_min_favorable_r", "0.25")))
    if max_holding_minutes <= 0 or stagnation_minutes < 0 or stagnation_min_favorable_r < 0:
        raise ValueError("invalid holding-time or stagnation exit configuration")

    for i in range(len(bars)):
        bar = bars[i]
        exited_this_bar = False
        if pos is not None:
            deadline = pos.entry_time + timedelta(minutes=max_holding_minutes)
            if bar.bar_start >= deadline:
                # If data resumes after the deadline, use the last completed pre-deadline close.
                # This avoids inventing a fill inside a data gap or exceeding the hard time cap.
                if bar.bar_start == deadline or i == 0:
                    exit_time, exit_price, reason = bar.bar_start, bar.open, "MAX_HOLD_TIME"
                else:
                    prior = bars[i - 1]
                    exit_time, exit_price, reason = prior.bar_end, prior.close, "MAX_HOLD_TIME_DATA_GAP"
                trades.append(_close(symbol, pos, exit_time, exit_price, reason, cost_values, costs_applicable))
                pos = None
                exited_this_bar = True
            elif bar.bar_end == deadline:
                # This bar closes exactly at the deadline, so its close is a valid time-stop fill.
                trades.append(_close(symbol, pos, deadline, bar.close, "MAX_HOLD_TIME", cost_values, costs_applicable))
                pos = None
                exited_this_bar = True
            elif bar.bar_end > deadline:
                # The deadline falls inside this OHLC bar. Do not use its high/low/close,
                # which may include post-deadline prices; exit at the last completed close.
                prior = bars[i - 1] if i > 0 else bar
                trades.append(_close(symbol, pos, prior.bar_end, prior.close,
                                     "MAX_HOLD_TIME_BAR_RESOLUTION", cost_values, costs_applicable))
                pos = None
                exited_this_bar = True
            else:
                stop_distance = abs(pos.entry_price - pos.stop)
                favourable = (bar.high - pos.entry_price) if pos.side > 0 else (pos.entry_price - bar.low)
                if stop_distance > 0:
                    pos.best_favorable_r = max(pos.best_favorable_r, favourable / stop_distance)
                hit = _intrabar_exit(pos, bar)
                if hit is not None:
                    price, reason = hit
                    trades.append(_close(symbol, pos, bar.bar_end, price, reason, cost_values, costs_applicable))
                    pos = None
                    exited_this_bar = True
                else:
                    held_minutes = (bar.bar_end - pos.entry_time).total_seconds() / 60
                    if (stagnation_minutes > 0 and held_minutes >= stagnation_minutes
                            and pos.best_favorable_r < stagnation_min_favorable_r):
                        trades.append(_close(symbol, pos, bar.bar_end, bar.close, "STAGNATION_TIME", cost_values, costs_applicable))
                        pos = None
                        exited_this_bar = True
        if pos is None and pending is not None:
            side, stop_dist, signal_target, entry_risk_pct, target_source, signal_close, entry_regime, entry_market_phase = pending
            # Structural targets remain fixed market levels. ATR targets preserve their
            # signal-time distance from the actual simulated fill.
            risk_budget = start_equity * entry_risk_pct
            requested_qty = risk_budget / stop_dist if stop_dist > 0 else Decimal(0)
            if profile not in _FRACTIONAL_QTY_PROFILES:
                requested_qty = Decimal(math.floor(requested_qty))
            fill = simulator.submit(bar, "buy" if side > 0 else "sell", "market", requested_qty)
            filled_qty = fill.quantity
            if profile not in _FRACTIONAL_QTY_PROFILES:
                filled_qty = Decimal(math.floor(filled_qty))
            if fill.status == "rejected" or filled_qty <= 0:
                rejected_entries += 1
                pending = None
                exited_this_bar = True
                continue
            if fill.status == "partial":
                partial_fills += 1
            entry_px = fill.fill_price
            target = (
                entry_px + side * abs(signal_target - signal_close)
                if target_source.startswith("ATR_") else signal_target
            )
            actual_rr = (Decimal(side) * (target - entry_px) / stop_dist) if stop_dist > 0 else Decimal(0)
            minimum_rr = Decimal(str(getattr(engine, "min_rr", "1.5")))
            if actual_rr <= 0 or actual_rr < minimum_rr:
                rejected_entries += 1
                pending = None
                exited_this_bar = True
                continue
            actual_risk_amount = filled_qty * stop_dist
            if actual_risk_amount > risk_budget:
                raise ValueError("simulated fill exceeds the configured risk budget")
            ecost = entry_costs(cost_values, filled_qty, entry_px) if costs_applicable else None
            stop = entry_px - side * stop_dist
            pos = Position(side, filled_qty, bar.bar_start, entry_px, stop, target,
                           actual_risk_amount, ecost, entry_regime=entry_regime,
                           entry_fill_status=fill.status, cost_status=coverage_status,
                           entry_market_phase=entry_market_phase)
            # The entry bar is part of the position lifecycle; never skip its stop/target range.
            hit = _intrabar_exit(pos, bar)
            if hit is not None:
                price, reason = hit
                trades.append(_close(symbol, pos, bar.bar_end, price, reason, cost_values, costs_applicable))
                pos = None
                exited_this_bar = True
            pending = None
        if pos is not None:
            regime = Regime(snaps[i].context_regime) if snaps[i].context_regime else Regime.CHOP_DAY
            sig = engine.evaluate(
                timestamp=bar.bar_end, symbol=symbol, snapshot=snaps[i], regime=regime,
                stale_feed_seconds=0, stale_threshold_seconds=10,
                minutes_to_close=minutes_to_close(bar.bar_end, profile),
                position_quantity=pos.qty * pos.side,
                previous_snapshot=snaps[i - 1] if i > 0 else None,
                position_stop=pos.stop, position_target=pos.target,
            )
            if sig.action.startswith("exit_"):
                trades.append(_close(symbol, pos, bar.bar_end, bar.close, sig.selected_fib_zone or "SIGNAL", cost_values, costs_applicable))
                pos = None
                exited_this_bar = True
        elif pos is None and pending is None and not exited_this_bar:
            regime = Regime(snaps[i].context_regime) if snaps[i].context_regime else Regime.CHOP_DAY
            sig = engine.evaluate(
                timestamp=bar.bar_end, symbol=symbol, snapshot=snaps[i], regime=regime,
                stale_feed_seconds=0, stale_threshold_seconds=10,
                minutes_to_close=minutes_to_close(bar.bar_end, profile),
                previous_snapshot=snaps[i - 1] if i > 0 else None,
                event_risk=event_risk_provider(bar.bar_end, symbol) if event_risk_provider else None,
            )
            if sig.action in ("enter_long", "enter_short") and snaps[i].atr:
                side = 1 if sig.action == "enter_long" else -1
                target = Decimal(sig.parameters["target"])
                target_source = str(sig.parameters.get("target_source", "STRUCTURE"))
                signal_close = snaps[i].last_close or bar.close
                policy_decision = policy.evaluate(
                    timestamp=bar.bar_end,
                    symbol=symbol,
                    regime=regime,
                    stale_feed_seconds=0,
                    stale_threshold_seconds=10,
                    minutes_to_close=minutes_to_close(bar.bar_end, profile),
                )
                effective_risk_pct = (
                    min(risk_pct, Decimal(str(policy_decision.max_risk_pct)))
                    if policy_decision.max_risk_pct is not None else risk_pct
                )
                # Reaction-zone sizing is part of the strategy contract, not display-only metadata.
                size_weight = Decimal(str(sig.parameters.get("size_weight", "1")))
                if size_weight <= 0 or size_weight > 1:
                    raise ValueError(f"invalid reaction-zone size_weight for {symbol}: {size_weight}")
                effective_risk_pct *= size_weight
                stop_mult = Decimal(sig.parameters.get("stop_mult", str(engine.stop_mult)))
                market_phase = classify_market_phase(regime, snaps[i], snaps[:i])
                pending = (side, snaps[i].atr * stop_mult, target, effective_risk_pct,
                           target_source, signal_close, regime.value, market_phase)

    if pos is not None:  # close any open position at the last bar
        last = bars[-1]
        trades.append(_close(symbol, pos, last.bar_end, last.close, "END_OF_DATA", cost_values, costs_applicable))

    return {"symbol": symbol, "profile": profile, "trades": trades,
            "net_available": net_available, "cost_status": coverage_status,
            "excluded_cost_fields": excluded, "estimated_cost_fields": cost_values.get("_estimated", []),
            "execution": {"partial_fills": partial_fills, "rejected_entries": rejected_entries},
            "start_equity": start_equity, "bars": len(bars)}


def _close(symbol: str, pos: Position, exit_time: datetime, price: Decimal, reason: str,
           v: dict[str, Any], costs_applicable: bool) -> Trade:
    gross = (price - pos.entry_price) * pos.qty * pos.side
    if costs_applicable and pos.entry_cost is not None:
        cost = pos.entry_cost + exit_costs(v, pos.qty, price)
        net = gross - cost
        r = net / pos.risk_amount if pos.risk_amount else None
    else:
        cost = None
        net = None
        r = gross / pos.risk_amount if pos.risk_amount else None
    holding_minutes = Decimal(str((exit_time - pos.entry_time).total_seconds())) / Decimal(60)
    return Trade(symbol, "long" if pos.side > 0 else "short", pos.qty, pos.entry_time, pos.entry_price,
                 exit_time, price, reason, pos.risk_amount, gross, cost, net, r, holding_minutes,
                 pos.entry_regime, pos.entry_fill_status, pos.cost_status, "unprocessed", pos.entry_market_phase)


def summarize(trades: list[Trade], start_equity: Decimal) -> dict[str, Any]:
    if not trades:
        return {"trades": 0, "cost_status": "no_trades"}
    statuses = {trade.cost_status for trade in trades}
    cost_status = ("fully_net" if statuses == {"fully_net"} else
                   "partial_net" if statuses.intersection({"fully_net", "partial_net"}) else "gross")
    def stats(values: list[Decimal]) -> dict[str, Any]:
        wins = [v for v in values if v > 0]
        losses = [v for v in values if v <= 0]
        gross_win = sum(wins, Decimal(0))
        gross_loss = -sum(losses, Decimal(0))
        equity = start_equity
        peak = equity
        max_dd = Decimal(0)
        for v in values:
            equity += v
            peak = max(peak, equity)
            if peak > 0:
                max_dd = max(max_dd, (peak - equity) / peak)
        return {"trades": len(values), "win_rate": round(len(wins) / len(values), 4),
                "profit_factor": float(gross_win / gross_loss) if gross_loss else None,
                "total_pnl": float(sum(values, Decimal(0))),
                "return_pct": float(sum(values, Decimal(0)) / start_equity * 100),
                "max_drawdown_pct": float(max_dd * 100),
                "expectancy": float(sum(values, Decimal(0)) / Decimal(len(values)))}
    net_vals = [t.net_pnl for t in trades if t.net_pnl is not None]
    gross_vals = [t.gross_pnl for t in trades]
    out = {"cost_status": cost_status, "gross": stats(gross_vals)}
    if net_vals and len(net_vals) == len(trades):
        out["net"] = stats(net_vals)
    else:
        out["net"] = None
    out["by_exit_reason"] = {
        reason: stats([t.gross_pnl for t in trades if t.exit_reason == reason])
        for reason in sorted({t.exit_reason for t in trades})
    }
    out["by_side"] = {side: stats([t.gross_pnl for t in trades if t.side == side])
                      for side in {t.side for t in trades}}
    out["fill_status_counts"] = {status: sum(1 for trade in trades if trade.fill_status == status)
                                 for status in ("filled", "partial")}
    out["by_market_phase"] = {
        phase: stats([t.gross_pnl for t in trades if t.market_phase == phase])
        for phase in sorted({t.market_phase for t in trades})
    }
    out["by_market_phase_side"] = {
        phase: {
            side: stats([t.gross_pnl for t in trades if t.market_phase == phase and t.side == side])
            for side in ("long", "short")
            if any(t.market_phase == phase and t.side == side for t in trades)
        }
        for phase in sorted({t.market_phase for t in trades})
    }
    out["by_regime"] = {
        regime: stats([t.gross_pnl for t in trades if t.regime == regime])
        for regime in sorted({t.regime for t in trades})
    }
    out["by_regime_side"] = {
        regime: {
            side: stats([t.gross_pnl for t in trades if t.regime == regime and t.side == side])
            for side in ("long", "short")
            if any(t.regime == regime and t.side == side for t in trades)
        }
        for regime in sorted({t.regime for t in trades})
    }
    durations = sorted(t.holding_minutes for t in trades)
    middle = len(durations) // 2
    median = (durations[middle] if len(durations) % 2
              else (durations[middle - 1] + durations[middle]) / Decimal(2))
    out["holding_minutes"] = {
        "average": float(sum(durations, Decimal(0)) / Decimal(len(durations))),
        "median": float(median),
        "maximum": float(max(durations)),
    }
    return out



def _apply_portfolio_constraints(
    results: list[dict[str, Any]],
    markets: dict[str, Any],
    costs: dict[str, Any],
    capital: Decimal,
    risk_pct: Decimal,
    allow_estimates: bool,
) -> tuple[list[Trade], list[dict[str, Any]], dict[str, Any]]:
    market_config = markets["markets"]
    allocations = {
        name: Decimal(str(config.get("allocation_pct", 0)))
        for name, config in market_config.items()
    }
    cluster_caps = {
        str(config["cluster"]): Decimal(str(config["cluster_risk_cap"]))
        for config in market_config.values()
        if config.get("cluster") and config.get("cluster_risk_cap") is not None
    }
    engine_config = markets.get("engine", {})
    max_concurrent = int(engine_config.get("max_concurrent_positions", 10))
    total_risk_cap = Decimal(str(engine_config.get("max_total_open_risk_pct", "0.01")))
    ledger = PortfolioLedger(
        capital, allocations, cluster_caps,
        max_concurrent_positions=max_concurrent,
        max_total_open_risk_pct=total_risk_cap,
    )
    profiles = {item["symbol"]: item["profile"] for item in results}
    candidates = sorted(
        (trade for item in results for trade in item["trades"]),
        key=lambda trade: (trade.entry_time, trade.symbol, trade.exit_time),
    )
    accepted: list[Trade] = []
    rejected: list[dict[str, Any]] = []
    scaled_count = 0
    rejection_counts: dict[str, int] = {}
    for trade in candidates:
        profile = profiles[trade.symbol]
        config = market_config[profile]
        stop_distance = trade.risk_amount / trade.qty if trade.qty > 0 else Decimal(0)
        decision = ledger.reserve(
            timestamp=trade.entry_time,
            exit_time=trade.exit_time,
            symbol=trade.symbol,
            market=profile,
            cluster=str(config.get("cluster", "")),
            requested_quantity=trade.qty,
            entry_price=trade.entry_price,
            stop_distance=stop_distance,
            max_risk_amount=min(trade.risk_amount, capital * risk_pct),
            whole_shares=profile not in _FRACTIONAL_QTY_PROFILES,
        )
        if not decision.accepted:
            rejection_counts[decision.reason or "unknown"] = rejection_counts.get(decision.reason or "unknown", 0) + 1
            rejected.append({
                "symbol": trade.symbol,
                "entry_time": trade.entry_time.isoformat(),
                "reason": decision.reason,
                "requested_quantity": str(trade.qty),
                "requested_notional": str(trade.qty * trade.entry_price),
            })
            continue
        if decision.status == "scaled":
            scaled_count += 1
            gross = (trade.exit_price - trade.entry_price) * decision.quantity * (
                Decimal(1) if trade.side == "long" else Decimal(-1)
            )
            cost_values, _excluded = _cost_model(costs, profile, allow_estimates, trade.symbol)
            if profile in _EQUITY_COST_PROFILES:
                cost = (
                    entry_costs(cost_values, decision.quantity, trade.entry_price)
                    + exit_costs(cost_values, decision.quantity, trade.exit_price)
                )
                net = gross - cost
            else:
                cost = None
                net = None
            r_multiple = (
                net / decision.risk_amount if net is not None and decision.risk_amount
                else gross / decision.risk_amount if decision.risk_amount else None
            )
            accepted.append(replace(
                trade, qty=decision.quantity, risk_amount=decision.risk_amount,
                gross_pnl=gross, cost=cost, net_pnl=net, r_multiple=r_multiple,
                portfolio_status="scaled",
            ))
        else:
            accepted.append(replace(trade, portfolio_status="accepted"))
    return accepted, rejected, {
        "capital": str(capital),
        "max_concurrent_positions": max_concurrent,
        "max_total_open_risk_pct": str(total_risk_cap),
        "candidate_trades": len(candidates),
        "accepted_trades": len(accepted),
        "scaled_trades": scaled_count,
        "rejected_trades": len(rejected),
        "rejection_counts": rejection_counts,
        "simultaneous_priority": "entry_time_then_symbol_then_exit_time",
    }


def run_dataset(directory: str | Path, interval_seconds: int = 300, start_equity: Decimal = Decimal(100000),
                risk_pct: Decimal = Decimal("0.0025"), allow_estimates: bool = False,
                profile_map: dict[str, str] | None = None, instrument_symbols: dict[int, str] | None = None,
                markets_path: str | Path = "config/markets_17.yaml",
                costs_path: str | Path = "config/costs.yaml",
                strategy_path: str | Path = "config/strategies/confluence_pullback.yaml",
                event_risk_provider: Callable[[datetime, str], EventRiskAssessment | None] | None = None,
                baseline_scope: bool = True) -> dict[str, Any]:
    directory = Path(directory)
    instrument_symbols = instrument_symbols or _instrument_symbols(directory)
    records = load_bar_records(directory / "bars.csv")
    grouped = group_bars(records)

    import yaml
    markets = yaml.safe_load(Path(markets_path).read_text(encoding="utf-8"))
    if baseline_scope:
        allowed_profiles = {"US_EQUITIES", "US_COMMODITIES"}
        if profile_map is None:
            profile_map = {
                symbol: profile
                for profile in sorted(allowed_profiles)
                for symbol in markets["markets"][profile]["core_symbols"]
            }
        if any(profile not in allowed_profiles for profile in profile_map.values()):
            raise ValueError("Phase 2 baseline is restricted to US equities and US-listed gold/oil ETF proxies")
    else:
        profile_map = profile_map or DEFAULT_PROFILE
        for symbol, profile in profile_map.items():
            core_symbols = markets["markets"][profile]["core_symbols"]
            if symbol not in core_symbols:
                core_symbols.append(symbol)
    policy = TradingPolicy(markets, "dataset-run")
    engine = ConfluenceV1.from_yaml(strategy_path, policy)
    configured_signal_seconds = int(engine.strategy["timeframes"]["signal_mins"]) * 60
    if interval_seconds != configured_signal_seconds:
        raise ValueError(
            f"dataset interval {interval_seconds}s does not match configured "
            f"{engine.strategy['timeframes']['signal_mins']}m signal timeframe"
        )
    if start_equity <= 0:
        raise ValueError("portfolio capital must be positive")
    if baseline_scope and start_equity > Decimal(100000):
        raise ValueError("Phase 2 portfolio capital cannot exceed USD 100,000")
    if baseline_scope:
        for symbol in instrument_symbols.values():
            profile = profile_map.get(symbol)
            if profile is None:
                raise ValueError(f"instrument {symbol} is outside the Phase 2 baseline universe")
            if symbol not in markets["markets"][profile]["core_symbols"]:
                raise ValueError(f"instrument {symbol} is not approved in {profile}.core_symbols")

    costs = load_costs(costs_path)
    results = []
    for iid, symbol in sorted(instrument_symbols.items()):
        bars = grouped.get((iid, interval_seconds), [])
        if not bars:
            continue
        profile = profile_map[symbol]
        results.append(run_symbol(symbol, bars, profile, engine, policy, costs, start_equity,
                                  risk_pct, allow_estimates, event_risk_provider=event_risk_provider))

    all_trades, portfolio_rejections, portfolio_summary = _apply_portfolio_constraints(
        results, markets, costs, start_equity, risk_pct, allow_estimates
    )
    accepted_by_symbol = {
        symbol: [trade for trade in all_trades if trade.symbol == symbol]
        for symbol in (item["symbol"] for item in results)
    }
    return {
        "net_definition": (
            "gross is before costs; partial_net applies available cost components but has excluded "
            "or estimated fields; fully_net is permitted only when every required cost field is verified "
            "and applied. Portfolio sizing is applied chronologically against shared capital."
        ),
        "dataset": str(directory),
        "interval_seconds": interval_seconds,
        "portfolio_capital": str(start_equity),
        "risk_pct_per_trade": float(risk_pct),
        "allow_estimates": allow_estimates,
        "baseline_scope": baseline_scope,
        "evidence_class": _dataset_evidence_class(directory),
        "per_symbol": {
            item["symbol"]: {
                "bars": item["bars"], "profile": item["profile"],
                "net_costs_available": item["net_available"], "cost_status": item["cost_status"],
                "cost_fields_excluded": item["excluded_cost_fields"],
                "estimated_cost_fields": item["estimated_cost_fields"], "execution": item["execution"],
                "trade_status": "traded" if accepted_by_symbol[item["symbol"]] else "no_trades",
                "no_trade_interpretation": (
                    None if accepted_by_symbol[item["symbol"]] else
                    "No eligible trades were produced; this is not evidence of profitability or unprofitability."
                ),
                "summary": summarize(accepted_by_symbol[item["symbol"]], start_equity),
            } for item in results
        },
        "portfolio": portfolio_summary,
        "portfolio_rejections": portfolio_rejections,
        "combined": summarize(all_trades, start_equity),
        "_trades": all_trades,
    }


def _dataset_evidence_class(directory: Path) -> str:
    """Read explicit fixture metadata; never treat synthetic tests as market evidence."""
    manifest = directory / "manifest.json"
    if not manifest.exists():
        return "unverified_historical_dataset"
    try:
        metadata = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "unverified_dataset_manifest"
    if metadata.get("contains_real_market_data") is False or metadata.get("performance_claims_allowed") is False:
        return "synthetic_software_fixture_only"
    return "source_declared_dataset"


def _instrument_symbols(directory: Path) -> dict[int, str]:
    with (directory / "instruments.csv").open(newline="", encoding="utf-8") as handle:
        return {int(r["instrument_id"]): r["canonical_symbol"] for r in csv.DictReader(handle)}


def write_outputs(result: dict[str, Any], out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    trades = result.pop("_trades")
    ledger = out / "trades.csv"
    with ledger.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["symbol", "side", "qty", "entry_time", "entry_price", "exit_time", "exit_price",
                         "exit_reason", "holding_minutes", "risk_amount", "gross_pnl", "cost", "net_pnl", "r_multiple", "regime", "market_phase", "fill_status", "cost_status", "portfolio_status"])
        for t in trades:
            writer.writerow([t.symbol, t.side, t.qty, t.entry_time.isoformat(), t.entry_price,
                             t.exit_time.isoformat(), t.exit_price, t.exit_reason, t.holding_minutes, t.risk_amount,
                             t.gross_pnl, "" if t.cost is None else t.cost,
                             "" if t.net_pnl is None else t.net_pnl, "" if t.r_multiple is None else t.r_multiple,
                              t.regime, t.market_phase, t.fill_status, t.cost_status, t.portfolio_status])
    summary = out / "summary.json"
    summary.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    result["_trades"] = trades
    return summary
