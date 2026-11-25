"""Deterministic audited summary for the 252-session local backtest."""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.actors import ActorRuntime
from app.execution import DemoBroker
from app.feeds import Candle, FeedStore
from app.regime import Regime


@dataclass(frozen=True, slots=True)
class AuditedBacktestReport:
    days: int
    starting_kitty: Decimal
    ending_kitty: Decimal
    net_pnl: Decimal
    total_trades: int
    win_rate: Decimal
    profit_factor: Decimal
    max_drawdown: Decimal
    buy_signals: int
    sell_signals: int
    held_signals: int
    audit_warning: str

    def as_table(self) -> dict[str, str]:
        pct = ((self.ending_kitty / self.starting_kitty) - Decimal(1)) * Decimal(100)
        return {
            "Metric": "Multi-Market Audited Run (Deterministic Fixture)",
            "Ending Kitty": f"${self.ending_kitty:.2f} ({pct:+.2f}%)",
            "Net P&L": f"${self.net_pnl:+.2f}",
            "Total Trades": f"{self.total_trades} ({Decimal(self.total_trades) / Decimal(self.days):.2f}/day)",
            "Overall Win Rate": f"{self.win_rate * 100:.2f}%",
            "Overall Profit Factor": f"{self.profit_factor:.2f}",
            "Max Drawdown": f"-{self.max_drawdown * 100:.2f}%",
        }


def run_audited_backtest(store: FeedStore, actor: ActorRuntime, broker: DemoBroker,
                         *, days: int = 252, ticks_per_day: int = 20,
                         warmup_fraction: float = 0.25,
                         interval_seconds: float = 1.0) -> AuditedBacktestReport:
    if days <= 0 or ticks_per_day <= 0:
        raise ValueError("days and ticks_per_day must be positive")
    if not 0 < warmup_fraction < 1:
        raise ValueError("warmup_fraction must be between 0 and 1")

    starting = broker.starting_cash
    base = datetime(2026, 11, 20, tzinfo=UTC)
    equity_curve = [starting]
    trade_pnl: list[Decimal] = []
    buy_signals = sell_signals = held_signals = 0
    buy_entries: dict[str, tuple[Decimal, Decimal]] = {}
    warmup_windows: dict[str, list[Candle]] = {}

    for spec in store.config.symbols:
        if spec.enabled:
            bars = _load_fixture_stream(store, spec.symbol)
            if bars:
                warmup_windows[spec.symbol] = [_backtest_candle(candle)
                                               for candle in bars[:int(len(bars) * warmup_fraction)]]

    for day in range(days):
        session_time = base + timedelta(days=day)
        marks: dict[str, Decimal] = {}

        for tick_index in range(ticks_per_day):
            timestamp = session_time + timedelta(seconds=tick_index)
            for spec in store.config.symbols:
                if not spec.enabled:
                    continue
                bars = _load_fixture_stream(store, spec.symbol)
                if not bars:
                    continue
                warmup = int(len(bars) * warmup_fraction)
                window = warmup_windows.setdefault(spec.symbol, [_backtest_candle(candle)
                                                                  for candle in bars[:warmup]])
                index = warmup + ((day * ticks_per_day + tick_index) % max(1, len(bars) - warmup))
                source = bars[index % len(bars)]
                candle = Candle(timestamp, timestamp + timedelta(seconds=60), source.open,
                                source.high, source.low, source.close, source.volume,
                                source.trade_count, "deterministic_audited_fixture", False)
                window = (window[-499:] + [candle])
                warmup_windows[spec.symbol] = window
                store.put(spec.symbol, "1m", window)

            position_quantities = {position.symbol: position.quantity
                                   for position in broker.positions}
            cards = actor.evaluate_once(allow_synthetic=True,
                                        position_quantities=position_quantities)
            marks = _marks(store)
            for card in cards:
                action = getattr(card, "display_action", "HOLD")
                symbol = getattr(card, "symbol", "")
                if action == "BUY":
                    buy_signals += 1
                elif action == "SELL":
                    sell_signals += 1
                else:
                    held_signals += 1

                mark = marks.get(symbol)
                if action not in {"BUY", "SELL"} or mark is None:
                    continue
                position = next((pos for pos in broker.positions if pos.symbol == symbol), None)
                if action == "BUY" and position is not None:
                    continue
                if action == "SELL" and position is None:
                    continue

                side = "buy" if action == "BUY" else "sell"
                order = broker.submit_signal(
                    signal_id=getattr(card, "signal_id", f"audit:{day}:{tick_index}:{symbol}"),
                    symbol=symbol, side=side, quantity=Decimal(1), timestamp=timestamp,
                    minutes_to_close=120, stale_feed_seconds=1,
                    stale_threshold_seconds=actor.stale_threshold_seconds,
                    regime=Regime.TREND_DAY, limit_price=mark,
                )
                if order.state == "rejected":
                    continue
                fill = broker.fill(order.order_id, price=mark, quantity=Decimal(1), timestamp=timestamp)
                if side == "buy":
                    buy_entries[symbol] = (fill.price, fill.fee)
                else:
                    buy = buy_entries.pop(symbol, None)
                    if buy is not None:
                        trade_pnl.append((fill.price - buy[0]) - buy[1] - fill.fee)

            equity_curve.append(broker.equity(marks))
            if interval_seconds > 0:
                time.sleep(interval_seconds)

        if marks:
            flat_fills = broker.flatten(timestamp=session_time, marks=marks)
            sell_signals += len(flat_fills)
            for fill in flat_fills:
                buy = buy_entries.pop(fill.symbol, None)
                if buy is not None:
                    trade_pnl.append((fill.price - buy[0]) - buy[1] - fill.fee)

    ending = broker.equity(marks)
    wins = [pnl for pnl in trade_pnl if pnl > 0]
    losses = [pnl for pnl in trade_pnl if pnl < 0]
    gross_profit = sum(wins, Decimal(0))
    gross_loss = abs(sum(losses, Decimal(0)))
    profit_factor = gross_profit / gross_loss if gross_loss else (gross_profit if gross_profit else Decimal(1))
    win_rate = Decimal(len(wins)) / Decimal(len(trade_pnl)) if trade_pnl else Decimal(0)

    peak = starting
    max_drawdown = Decimal(0)
    for equity in equity_curve:
        peak = max(peak, equity)
        if peak:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)

    return AuditedBacktestReport(
        days=days,
        starting_kitty=starting,
        ending_kitty=ending,
        net_pnl=ending - starting,
        total_trades=len(trade_pnl),
        win_rate=win_rate,
        profit_factor=profit_factor,
        max_drawdown=max_drawdown,
        buy_signals=buy_signals,
        sell_signals=sell_signals,
        held_signals=held_signals,
        audit_warning="Synthetic deterministic fixture audit; not broker-verified live or historical performance",
    )


def _load_fixture_stream(store: FeedStore, symbol: str) -> list[Candle]:
    spec = next((item for item in store.config.symbols if item.symbol == symbol), None)
    if spec is None:
        return []
    fixture_symbol = "SPX" if spec.symbol == "^GSPC" else spec.symbol
    safe = fixture_symbol.replace("=", "_").replace("/", "_")
    path = store.config.fallback_fixture / "bars" / f"{safe}-1m.csv"
    if not path.is_file():
        return []
    return store._read_fixture(path, "1m")


def _backtest_candle(candle: Candle) -> Candle:
    return Candle(candle.bar_start, candle.bar_end, candle.open, candle.high,
                  candle.low, candle.close, candle.volume, candle.trade_count,
                  "deterministic_audited_fixture", False)


def _marks(store: FeedStore) -> dict[str, Decimal]:
    return {spec.symbol: bars[-1].close for spec in store.config.symbols
            if (bars := store.bars(spec.symbol, "1m", 1))}
