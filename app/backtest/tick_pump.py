"""Timed fixture tick replay with actor and demo-broker execution."""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from app.actors import ActorRuntime
from app.execution import DemoBroker
from app.feeds import Candle, FeedStore
from app.regime import Regime


@dataclass(frozen=True, slots=True)
class TickPumpResult:
    ticks_processed: int
    warmup_ticks: int
    decisions: tuple[object, ...]
    audit: tuple[object, ...]


class TickPumpRuntime:
    """Initialize each symbol from 25% of fixture history, then pump ticks once per second."""

    def __init__(self, store: FeedStore, actor: ActorRuntime, broker: DemoBroker,
                 *, warmup_fraction: float = 0.25, interval_seconds: float = 1.0,
                 quantity_per_trade: Decimal = Decimal(1), regime: Regime = Regime.TREND_DAY) -> None:
        if not 0 < warmup_fraction < 1:
            raise ValueError("warmup_fraction must be between 0 and 1")
        if interval_seconds < 0:
            raise ValueError("interval_seconds cannot be negative")
        if quantity_per_trade <= 0:
            raise ValueError("quantity_per_trade must be positive")
        self.store = store
        self.actor = actor
        self.broker = broker
        self.warmup_fraction = warmup_fraction
        self.interval_seconds = interval_seconds
        self.quantity_per_trade = quantity_per_trade
        self.regime = regime
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._decisions: list[object] = []
        self._audit: list[dict[str, object]] = []
        self._log = logging.getLogger("x2.backtest.tick-pump")

    def _fixture_candles(self, symbol: str) -> list[Candle]:
        fixture = self.store.config.fallback_fixture
        spec = next((item for item in self.store.config.symbols if item.symbol == symbol), None)
        if spec is None:
            return []
        fixture_symbol = "SPX" if spec.symbol == "^GSPC" else spec.symbol
        safe = fixture_symbol.replace("=", "_").replace("/", "_")
        interval = "1m" if "1m" in spec.timeframes else spec.timeframes[0]
        return self.store._read_fixture(fixture / "bars" / f"{safe}-{interval}.csv", interval)

    def run(self, *, max_ticks: int | None = None) -> TickPumpResult:
        streams: dict[str, list[Candle]] = {}
        for spec in self.store.config.symbols:
            if spec.enabled:
                candles = self._fixture_candles(spec.symbol)
                if candles:
                    streams[spec.symbol] = candles
        if not streams:
            raise RuntimeError("no fixture tick streams available")

        per_stream_limit = max_ticks
        warmup_counts = {
            symbol: int(len(candles) * self.warmup_fraction)
            for symbol, candles in streams.items()
        }
        warmup_ticks = min(warmup_counts.values())

        pump_buffers: dict[str, list[Candle]] = {}
        ticks_processed = 0
        for symbol, candles in streams.items():
            count = min(warmup_counts[symbol], len(candles))
            pump = [Candle(c.bar_start, c.bar_end, c.open, c.high, c.low, c.close,
                           c.volume, c.trade_count, "backtest_fixture", False)
                    for c in candles[:count]]
            pump_buffers[symbol] = pump
            self.store.put(symbol, "1m", pump)

        first_cards = self.actor.evaluate_once()
        self._record_decisions(first_cards)

        for index in range(warmup_ticks, min(len(candles) for candles in streams.values())):
            if per_stream_limit is not None and ticks_processed >= per_stream_limit:
                break
            tick_card_count = 0
            for symbol, candles in streams.items():
                if index >= len(candles):
                    continue
                source = candles[index]
                tick = Candle(source.bar_start, source.bar_end, source.open, source.high,
                              source.low, source.close, source.volume, source.trade_count,
                              "backtest_fixture", False)
                pump_buffers[symbol].append(tick)
                self.store.put(symbol, "1m", pump_buffers[symbol])

            cards = self.actor.evaluate_once()
            self._record_decisions(cards)
            tick_card_count += len(cards)
            marks = self._current_marks()
            candidates = self.actor.scanner.discover(regime=self.regime.value)
            reaction = {str(card.symbol): card.display_action in {"BUY", "SELL"} for card in cards}
            stage_cards = self.actor.scanner.evaluate(candidates, reaction)
            trade_samples = [{"side": fill.side, "realized_pnl": str((marks.get(fill.symbol, fill.price) - fill.price) * fill.quantity)}
                             for fill in self.broker.fills]
            analysis = self.actor.analyzer.analyze(trade_samples)
            self._log.info("actor02_cards=%d actor06_trade_count=%d", len(stage_cards), analysis["trade_count"])
            self._execute_cards(cards)
            ticks_processed += 1
            self._log.info("tick=%d cards=%d actors=02,04,06", ticks_processed, tick_card_count)
            if self.interval_seconds:
                time.sleep(self.interval_seconds)
        return TickPumpResult(ticks_processed, warmup_ticks, tuple(self._decisions), tuple(self._audit))

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self.run, name="x2-tick-pump", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _record_decisions(self, cards: Iterable[object]) -> None:
        for card in cards:
            self._decisions.append(card)

    def _execute_cards(self, cards: Iterable[object]) -> None:
        for card in cards:
            action = getattr(card, "display_action", "HOLD")
            symbol = getattr(card, "symbol", "")
            if action not in {"BUY", "SELL"} or not symbol:
                continue
            position = next((pos for pos in self.broker.positions if pos.symbol == symbol), None)
            if action == "BUY" and position is not None:
                continue
            if action == "SELL" and position is None:
                continue
            timestamp = getattr(card, "timestamp", datetime.now(UTC))
            marks = self._current_marks()
            mark = marks.get(symbol)
            if mark is None:
                continue
            side = "buy" if action == "BUY" else "sell"
            order = self.broker.submit_signal(
                signal_id=getattr(card, "signal_id", f"tick:{timestamp.isoformat()}:{symbol}"),
                symbol=symbol,
                side=side,
                quantity=self.quantity_per_trade,
                timestamp=timestamp,
                minutes_to_close=120,
                stale_feed_seconds=1,
                stale_threshold_seconds=self.actor.stale_threshold_seconds,
                regime=self.regime,
                limit_price=mark,
            )
            if order.state == "rejected":
                self._audit.append({"timestamp": timestamp.isoformat(), "symbol": symbol,
                                    "action": action, "decision": getattr(card, "reason", ""),
                                    "order_state": "rejected", "reason": order.reason})
                continue
            fill = self.broker.fill(order.order_id, price=mark, quantity=self.quantity_per_trade, timestamp=timestamp)
            self._audit.append({"timestamp": timestamp.isoformat(), "symbol": symbol,
                                "action": action, "decision": getattr(card, "reason", ""),
                                "order_id": order.order_id, "fill_id": fill.fill_id,
                                "price": str(fill.price), "quantity": str(fill.quantity),
                                "parameters": dict(getattr(card, "parameters", {}))})

    def _current_marks(self) -> dict[str, Decimal]:
        result: dict[str, Decimal] = {}
        for spec in self.store.config.symbols:
            bars = self.store.bars(spec.symbol, "1m", 1)
            if bars:
                result[spec.symbol] = bars[-1].close
        return result
