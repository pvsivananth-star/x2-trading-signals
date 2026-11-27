"""Run the Confluence_v1 strategy over a local bars dataset (synthetic or licensed-local).

Execution model (conservative, documented):
- Signals are evaluated on the close of each bar using only bars that have closed.
- Entries fill at the OPEN of the next bar, with a stop placed 1 ATR from the fill and the
  structural target from the signal card.
- Stops and targets are checked intrabar on each later bar; when both are hit in one bar the
  stop is assumed to fill first.
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
from datetime import datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.backtest.bars import Bar
from app.costs import CostBlockedError, _field, load_costs
from app.features import FeatureSnapshot, feature_snapshot
from app.policy import TradingPolicy
from app.real_data import group_bars, load_bar_records
from app.regime import Regime
from app.strategy import ConfluenceV1

# Synthetic instruments inherit the threshold profile of a real market, so the gates are
# exercised with production-like numbers. The mapping is explicit and reported.
DEFAULT_PROFILE = {"SIM-EQ": "US_EQUITIES", "SIM-COIN": "CRYPTO_MAJORS"}
COST_SECTION = {"US_EQUITIES": "US_EQUITIES", "CRYPTO_MAJORS": "CRYPTO_BTC_SPOT"}
COST_FIELDS = {
    "US_EQUITIES": (
        "commission_per_share", "commission_min_per_order", "commission_max_pct_of_trade",
        "sec_fee_sell_rate", "finra_taf_sell_per_share", "finra_cat_per_share",
        "clearing_nscc_dtc_per_share", "spread_pct_full", "slippage_pct_per_side",
        "exchange_fee_per_share",
    ),
    "CRYPTO_MAJORS": ("commission", "spread_pct_full"),
}
EQUITY_SESSION_CLOSE = time(21, 0)  # 16:00 ET, UTC during winter time in the fixture


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


def minutes_to_close(bar_end: datetime, profile: str) -> int | None:
    """Minutes from the bar end to the regular close. None for 24x7 markets (no session flat)."""
    if profile != "US_EQUITIES":
        return None
    close = datetime.combine(bar_end.date(), EQUITY_SESSION_CLOSE, tzinfo=bar_end.tzinfo)
    return int((close - bar_end).total_seconds() // 60)


def _cost_model(costs: dict[str, Any], profile: str, allow_estimates: bool) -> tuple[dict[str, Any], list[str]]:
    section = COST_SECTION[profile]
    estimates_enabled = allow_estimates or costs.get("allow_estimates") is True
    field_costs = {**costs, "allow_estimates": estimates_enabled}
    excluded: list[str] = []
    values: dict[str, Any] = {}
    for name in COST_FIELDS[profile]:
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
    if pos.side > 0:
        if bar.low <= pos.stop:
            return pos.stop, "HARD_STOP"
        if bar.high >= pos.target:
            return pos.target, "TARGET_HIT"
    else:
        if bar.high >= pos.stop:
            return pos.stop, "HARD_STOP"
        if bar.low <= pos.target:
            return pos.target, "TARGET_HIT"
    return None


def run_symbol(symbol: str, bars: list[Bar], profile: str, engine: ConfluenceV1, policy: TradingPolicy,
               costs: dict[str, Any], start_equity: Decimal, risk_pct: Decimal,
               allow_estimates: bool) -> dict[str, Any]:
    cost_values, excluded = _cost_model(costs, profile, allow_estimates)
    net_available = "commission_per_share" not in excluded and profile == "US_EQUITIES"
    snaps: list[FeatureSnapshot] = [feature_snapshot(bars[: i + 1], bars[i].bar_end) for i in range(len(bars))]
    trades: list[Trade] = []
    pos: Position | None = None
    pending: tuple[int, str] | None = None  # (side, ...) entry decided on previous bar

    for i in range(20, len(bars)):
        bar = bars[i]
        if pos is not None:
            hit = _intrabar_exit(pos, bar)
            if hit is not None:
                price, reason = hit
                trades.append(_close(symbol, pos, bar.bar_end, price, reason, cost_values, net_available))
                pos = None
        if pos is None and pending is not None:
            side, stop_dist, target = pending
            entry_px = bar.open
            qty = (risk_amount := start_equity * risk_pct) / stop_dist
            if profile == "US_EQUITIES":
                qty = Decimal(math.floor(qty))
            if qty > 0:
                ecost = entry_costs(cost_values, qty, entry_px) if net_available else None
                stop = entry_px - side * stop_dist
                pos = Position(side, qty, bar.bar_start, entry_px, stop, target, risk_amount, ecost)
            pending = None
        if pos is not None:
            sig = engine.evaluate(
                timestamp=bar.bar_end, symbol=symbol, snapshot=snaps[i], regime=Regime.TREND_DAY,
                stale_feed_seconds=0, stale_threshold_seconds=10,
                minutes_to_close=minutes_to_close(bar.bar_end, profile),
                position_quantity=pos.qty * pos.side, previous_snapshot=snaps[i - 1],
                position_stop=pos.stop, position_target=pos.target,
            )
            if sig.action.startswith("exit_"):
                trades.append(_close(symbol, pos, bar.bar_end, bar.close, sig.selected_fib_zone or "SIGNAL", cost_values, net_available))
                pos = None
        elif pos is None and pending is None:
            sig = engine.evaluate(
                timestamp=bar.bar_end, symbol=symbol, snapshot=snaps[i], regime=Regime.TREND_DAY,
                stale_feed_seconds=0, stale_threshold_seconds=10,
                minutes_to_close=minutes_to_close(bar.bar_end, profile), previous_snapshot=snaps[i - 1],
            )
            if sig.action in ("enter_long", "enter_short") and snaps[i].atr:
                side = 1 if sig.action == "enter_long" else -1
                target = Decimal(sig.parameters["target"])
                pending = (side, snaps[i].atr * engine.stop_mult, target)

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
        markets["markets"][profile]["core_symbols"] = list(markets["markets"][profile]["core_symbols"]) + [symbol]
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
