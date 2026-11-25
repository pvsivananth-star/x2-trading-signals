"""In-process feed actor loops that generate explainable decision cards."""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime

from app.backtest.bars import Bar
from app.features import feature_snapshot
from app.feeds import FeedStore
from app.policy import TradingPolicy
from app.regime import Regime, StaticRegimeProvider
from app.strategy import ConfluenceV1


class ActorRuntime:
    def __init__(self, store: FeedStore, policy: TradingPolicy, strategy: ConfluenceV1,
                 interval: str = "5m", stale_threshold_seconds: int = 300,
                 regime_provider: object | None = None) -> None:
        self.store = store
        self.policy = policy
        self.strategy = strategy
        self.interval = interval
        self.stale_threshold_seconds = stale_threshold_seconds
        self.regime_provider = regime_provider or StaticRegimeProvider(Regime.CHOP_DAY)
        self.cards: dict[str, object] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._log = logging.getLogger("x2.actors")

    def evaluate_once(self) -> list[object]:
        now = datetime.now(UTC)
        cards: list[object] = []
        for spec in self.store.config.symbols:
            if not spec.enabled:
                self._log.info("action=reject symbol=%s reason=feed_disabled", spec.symbol)
                continue
            candle_bars = self.store.bars(spec.symbol, self.interval, 500)
            stale = self.store.stale_seconds(spec.symbol, now)
            synthetic = bool(candle_bars and all(c.synthetic for c in candle_bars))
            if synthetic:
                self._log.info("action=reject symbol=%s reason=synthetic_feed_not_live", spec.symbol)
                continue
            if not candle_bars:
                self._log.info("action=reject symbol=%s reason=no_bars", spec.symbol)
                continue
            bars = [Bar(c.bar_start, c.bar_end, c.open, c.high, c.low, c.close,
                        c.volume, c.trade_count) for c in candle_bars]
            snapshot = feature_snapshot(bars, now)
            regime = self.regime_provider.regime_at(now, spec.symbol)
            card = self.strategy.evaluate(timestamp=now, symbol=spec.symbol, snapshot=snapshot,
                                          regime=regime, stale_feed_seconds=stale,
                                          stale_threshold_seconds=self.stale_threshold_seconds,
                                          live_mode=False)
            self.cards[spec.symbol] = card
            cards.append(card)
            if card.rejected_gates:
                self._log.info("action=reject symbol=%s card=%s gates=%s", spec.symbol,
                               card.signal_id, ",".join(card.rejected_gates))
            else:
                self._log.info("action=%s symbol=%s card=%s", card.action, spec.symbol, card.signal_id)
        return cards

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="x2-actors", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop.wait(5):
            self.evaluate_once()
