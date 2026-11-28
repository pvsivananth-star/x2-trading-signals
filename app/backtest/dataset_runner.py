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
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.backtest.bars import Bar
from app.costs import CostBlockedError, _field, load_costs
from app.features import (
    DEFAULT_SESSION_CLOSE,
    DEFAULT_SESSION_OPEN,
    DEFAULT_SESSION_TZ,
    FeatureSnapshot,
    _local_dt,
    _session_context,
    _zone,
    causal_atr,
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


def _cost_model(costs: dict[str, Any], profile: str, allow_estimates: bool) -> tuple[dict[str, Any], list[str]]:
    section = COST_SECTION.get(profile, profile)
    estimates_enabled = allow_estimates or costs.get("allow_estimates") is True
    field_costs = {**costs, "allow_estimates": estimates_enabled}
    excluded: list[str] = []
    values: dict[str, Any] = {}
    fields = COST_FIELDS.get(profile, ("commission", "spread_pct_full"))
    for name in fields:
        try:
            values[name] = _field(field_costs, section, name)
        except CostBlockedError:
            values[name] = Decimal(0)
            excluded.append(name)
    values["_excluded"] = excluded
    values["_slippage_on"] = estimates_enabled and "slippage_pct_per_side" not in excluded
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
    """Resolve OHLC exits conservatively; stop is first if both levels are touched."""
    if pos.side > 0:
        if bar.open <= pos.stop:
            return bar.open, "HARD_STOP_GAP"
        if bar.low <= pos.stop:
            return pos.stop, "HARD_STOP"
        if bar.high >= pos.target:
            return pos.target, "TARGET_HIT"
    else:
        if bar.open >= pos.stop:
            return bar.open, "HARD_STOP_GAP"
        if bar.high >= pos.stop:
            return pos.stop, "HARD_STOP"
        if bar.low <= pos.target:
            return pos.target, "TARGET_HIT"
    return None


def run_symbol(symbol: str, bars: list[Bar], profile: str, engine: ConfluenceV1, policy: TradingPolicy,
               costs: dict[str, Any], start_equity: Decimal, risk_pct: Decimal,
               allow_estimates: bool) -> dict[str, Any]:
    cost_values, excluded = _cost_model(costs, profile, allow_estimates)
    net_available = "commission_per_share" not in excluded and profile in _EQUITY_COST_PROFILES
    session_timezone, session_open, session_close = MARKET_SESSIONS.get(
        profile, (DEFAULT_SESSION_TZ, DEFAULT_SESSION_OPEN, DEFAULT_SESSION_CLOSE)
    )
    session = {
        "session_timezone": session_timezone,
        "session_open": session_open,
        "session_close": session_close,
    }
    snaps: list[FeatureSnapshot] = [
        feature_snapshot(bars[: i + 1], bars[i].bar_end, **session) for i in range(len(bars))
    ]
    trades: list[Trade] = []
    pos: Position | None = None
    pending: tuple[int, Decimal, Decimal, Decimal, str, Decimal] | None = None
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
                # At/after the deadline, use this bar's open; do not inspect later prices in the bar.
                trades.append(_close(symbol, pos, bar.bar_start, bar.open, "MAX_HOLD_TIME", cost_values, net_available))
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
                    trades.append(_close(symbol, pos, bar.bar_end, price, reason, cost_values, net_available))
                    pos = None
                    exited_this_bar = True
                else:
                    held_minutes = (bar.bar_end - pos.entry_time).total_seconds() / 60
                    if bar.bar_end >= deadline:
                        # If the deadline falls inside an OHLC bar, exact execution time is unknowable.
                        # Close at that bar's close and expose the bar-resolution limitation.
                        trades.append(_close(symbol, pos, bar.bar_end, bar.close, "MAX_HOLD_TIME", cost_values, net_available))
                        pos = None
                        exited_this_bar = True
                    elif (stagnation_minutes > 0 and held_minutes >= stagnation_minutes
                          and pos.best_favorable_r < stagnation_min_favorable_r):
                        trades.append(_close(symbol, pos, bar.bar_end, bar.close, "STAGNATION_TIME", cost_values, net_available))
                        pos = None
                        exited_this_bar = True
        if pos is None and pending is not None:
            side, stop_dist, signal_target, entry_risk_pct, target_source, signal_close = pending
            entry_px = bar.open
            # Structural targets remain fixed market levels. ATR open-space targets preserve
            # their signal-time distance from the actual fill. Reject gap entries that destroy RR.
            target = (
                entry_px + side * abs(signal_target - signal_close)
                if target_source == "ATR_OPEN_SPACE" else signal_target
            )
            actual_rr = (Decimal(side) * (target - entry_px) / stop_dist) if stop_dist > 0 else Decimal(0)
            minimum_rr = Decimal(str(getattr(engine, "min_rr", "1.5")))
            if actual_rr <= 0 or actual_rr < minimum_rr:
                pending = None
                exited_this_bar = True
                continue
            qty = (risk_amount := start_equity * entry_risk_pct) / stop_dist
            if profile not in _FRACTIONAL_QTY_PROFILES:
                qty = Decimal(math.floor(qty))
            if qty > 0:
                ecost = entry_costs(cost_values, qty, entry_px) if net_available else None
                stop = entry_px - side * stop_dist
                pos = Position(side, qty, bar.bar_start, entry_px, stop, target, risk_amount, ecost)
                # The entry bar is part of the position lifecycle; never skip its stop/target range.
                hit = _intrabar_exit(pos, bar)
                if hit is not None:
                    price, reason = hit
                    trades.append(_close(symbol, pos, bar.bar_end, price, reason, cost_values, net_available))
                    pos = None
                    exited_this_bar = True
            pending = None
        if pos is not None:
            regime = causal_regime(bars[: i + 1], bar.bar_end, **session)
            sig = engine.evaluate(
                timestamp=bar.bar_end, symbol=symbol, snapshot=snaps[i], regime=regime,
                stale_feed_seconds=0, stale_threshold_seconds=10,
                minutes_to_close=minutes_to_close(bar.bar_end, profile),
                position_quantity=pos.qty * pos.side,
                previous_snapshot=snaps[i - 1] if i > 0 else None,
                position_stop=pos.stop, position_target=pos.target,
            )
            if sig.action.startswith("exit_"):
                trades.append(_close(symbol, pos, bar.bar_end, bar.close, sig.selected_fib_zone or "SIGNAL", cost_values, net_available))
                pos = None
                exited_this_bar = True
        elif pos is None and pending is None and not exited_this_bar:
            regime = causal_regime(bars[: i + 1], bar.bar_end, **session)
            sig = engine.evaluate(
                timestamp=bar.bar_end, symbol=symbol, snapshot=snaps[i], regime=regime,
                stale_feed_seconds=0, stale_threshold_seconds=10,
                minutes_to_close=minutes_to_close(bar.bar_end, profile),
                previous_snapshot=snaps[i - 1] if i > 0 else None,
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
                pending = (side, snaps[i].atr * stop_mult, target, effective_risk_pct, target_source, signal_close)

    if pos is not None:  # close any open position at the last bar
        last = bars[-1]
        trades.append(_close(symbol, pos, last.bar_end, last.close, "END_OF_DATA", cost_values, net_available))

    return {"symbol": symbol, "profile": profile, "trades": trades,
            "net_available": net_available, "excluded_cost_fields": excluded,
            "start_equity": start_equity, "bars": len(bars)}


def _close(symbol: str, pos: Position, exit_time: datetime, price: Decimal, reason: str,
           v: dict[str, Any], net_available: bool) -> Trade:
    gross = (price - pos.entry_price) * pos.qty * pos.side
    if net_available and pos.entry_cost is not None:
        cost = pos.entry_cost + exit_costs(v, pos.qty, price)
        net = gross - cost
        r = net / pos.risk_amount if pos.risk_amount else None
    else:
        cost = None
        net = None
        r = gross / pos.risk_amount if pos.risk_amount else None
    return Trade(symbol, "long" if pos.side > 0 else "short", pos.qty, pos.entry_time, pos.entry_price,
                 exit_time, price, reason, pos.risk_amount, gross, cost, net, r)


def summarize(trades: list[Trade], start_equity: Decimal) -> dict[str, Any]:
    if not trades:
        return {"trades": 0}
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
                "max_drawdown_pct": float(max_dd * 100)}
    net_vals = [t.net_pnl for t in trades if t.net_pnl is not None]
    gross_vals = [t.gross_pnl for t in trades]
    out = {"gross": stats(gross_vals)}
    if net_vals and len(net_vals) == len(trades):
        out["net"] = stats(net_vals)
    else:
        out["net"] = None
    out["by_side"] = {side: stats([t.gross_pnl for t in trades if t.side == side])
                      for side in {t.side for t in trades}}
    return out


def run_dataset(directory: str | Path, interval_seconds: int = 300, start_equity: Decimal = Decimal(25000),
                risk_pct: Decimal = Decimal("0.0025"), allow_estimates: bool = False,
                profile_map: dict[str, str] | None = None, instrument_symbols: dict[int, str] | None = None,
                markets_path: str | Path = "config/markets_17.yaml",
                costs_path: str | Path = "config/costs.yaml",
                strategy_path: str | Path = "config/strategies/confluence_pullback.yaml") -> dict[str, Any]:
    directory = Path(directory)
    profile_map = profile_map or DEFAULT_PROFILE
    instrument_symbols = instrument_symbols or _instrument_symbols(directory)
    records = load_bar_records(directory / "bars.csv")
    grouped = group_bars(records)

    import yaml
    markets = yaml.safe_load(Path(markets_path).read_text(encoding="utf-8"))
    for symbol, profile in profile_map.items():
        core_symbols = markets["markets"][profile]["core_symbols"]
        if symbol not in core_symbols:
            core_symbols.append(symbol)
    policy = TradingPolicy(markets, "dataset-run")
    engine = ConfluenceV1.from_yaml(strategy_path, policy)
    costs = load_costs(costs_path)

    results = []
    for iid, symbol in sorted(instrument_symbols.items()):
        bars = grouped.get((iid, interval_seconds), [])
        if not bars:
            continue
        profile = profile_map[symbol]
        results.append(run_symbol(symbol, bars, profile, engine, policy, costs, start_equity, risk_pct, allow_estimates))

    all_trades = [t for r in results for t in r["trades"]]
    return {
        "net_definition": ("gross minus costs from config/costs.yaml. Fields listed in cost_fields_excluded "
                           "are treated as zero (not verified or estimate blocked); the result is NOT a full "
                           "net-of-all-costs figure unless that list is empty."),
        "dataset": str(directory),
        "interval_seconds": interval_seconds,
        "risk_pct_per_trade": float(risk_pct),
        "allow_estimates": allow_estimates,
        "per_symbol": {
            r["symbol"]: {
                "bars": r["bars"], "profile": r["profile"], "net_costs_available": r["net_available"],
                "cost_fields_excluded": r["excluded_cost_fields"], "summary": summarize(r["trades"], r["start_equity"]),
            } for r in results
        },
        "combined": summarize(all_trades, start_equity * len(results)) if results else {"trades": 0},
        "_trades": all_trades,
    }


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
                         "exit_reason", "risk_amount", "gross_pnl", "cost", "net_pnl", "r_multiple"])
        for t in trades:
            writer.writerow([t.symbol, t.side, t.qty, t.entry_time.isoformat(), t.entry_price,
                             t.exit_time.isoformat(), t.exit_price, t.exit_reason, t.risk_amount,
                             t.gross_pnl, "" if t.cost is None else t.cost,
                             "" if t.net_pnl is None else t.net_pnl, "" if t.r_multiple is None else t.r_multiple])
    summary = out / "summary.json"
    summary.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    result["_trades"] = trades
    return summary
