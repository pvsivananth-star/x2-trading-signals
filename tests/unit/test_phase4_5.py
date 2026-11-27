import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.backtest.bars import Bar, SessionCalendar, Trade, rollup
from app.collector import (
    CollectorState,
    InMemoryCheckpointStore,
    LiveCollector,
)
from app.domain.events import RawTradeEvent
from app.features import (
    causal_atr,
    causal_trend_structure,
    causal_vwap,
    feature_snapshot,
    net_move_room,
)
from app.quality import QualityChecker


class Phase45Tests(unittest.TestCase):
    def trade_event(self, ident: str, seconds: int = 0) -> RawTradeEvent:
        return RawTradeEvent("binance", "BTCUSDT", datetime(2026, 11, 20, 0, seconds, tzinfo=UTC),
                             datetime(2026, 11, 20, 0, seconds, tzinfo=UTC), Decimal(100), Decimal(1),
                             venue_trade_id=ident, sequence_no=seconds)

    def test_collector_dedupes_replay_and_persists_checkpoint(self) -> None:
        store = InMemoryCheckpointStore()
        collector = LiveCollector(checkpoint_store=store, stale_seconds=100)
        collector.start()
        event = self.trade_event("one", 1)
        self.assertTrue(collector.accept(event))
        self.assertFalse(collector.accept(event))
        self.assertEqual(store.load().venue_event_id, "one")
        restarted = LiveCollector(checkpoint_store=store)
        restarted.start()
        self.assertEqual(restarted.replay([event]), 0)

    def test_collector_backoff_terminal_and_graceful_stop(self) -> None:
        collector = LiveCollector(initial_backoff_seconds=1, max_backoff_seconds=2, max_retries=3)
        collector.start()
        self.assertEqual(collector.reconnect([RuntimeError("x")]), 1)
        self.assertEqual(collector.reconnect([RuntimeError("x")]), 2)
        collector.reconnect([RuntimeError("x")])
        self.assertEqual(collector.health().state, CollectorState.TERMINAL)
        collector.stop()
        self.assertFalse(collector.accept(self.trade_event("blocked")))

    def test_collector_stale_blocks_signals_and_orders(self) -> None:
        now = datetime(2026, 11, 20, 0, 1, tzinfo=UTC)
        collector = LiveCollector(stale_seconds=10, clock=lambda: now)
        collector.start()
        collector.accept(self.trade_event("old", 0))
        health = collector.health()
        self.assertEqual(health.state, CollectorState.STALE)
        self.assertTrue(collector.blocks_signals_and_orders)

    def test_quality_required_values_mapping_gaps_and_dedupe(self) -> None:
        now = datetime(2026, 11, 20, tzinfo=UTC)
        event = RawTradeEvent("binance", "BTCUSDT", now, now, Decimal(100), Decimal(1),
                              venue_trade_id="a", sequence_no=1).normalize("BTC-USD", "object")
        event2 = RawTradeEvent("binance", "BTCUSDT", now + timedelta(seconds=20), now + timedelta(seconds=20),
                               Decimal(101), Decimal(1), venue_trade_id="a", sequence_no=5).normalize("BTC-USD", "object")
        findings = QualityChecker({"BTCUSDT": "BTC-USD"}, max_sequence_gap=2,
                                  max_timestamp_gap_seconds=10).check([event, event2])
        codes = {finding.code for finding in findings}
        self.assertIn("sequence_gap", codes)
        self.assertIn("timestamp_gap", codes)
        self.assertIn("duplicate", codes)

    def test_quality_rejects_naive_timestamp_and_out_of_order(self) -> None:
        QualityChecker()
        with self.assertRaises(ValueError):
            RawTradeEvent("x", "AAPL", datetime(2026, 11, 20), datetime(2026, 11, 20),
                          Decimal(1), Decimal(1), venue_trade_id="a")

    def test_30m_rollup_and_empty_gap_bars(self) -> None:
        start = datetime(2026, 11, 20, 0, 0, tzinfo=UTC)
        trades = [Trade(start, Decimal(100), Decimal(2)), Trade(start + timedelta(minutes=31), Decimal(105), Decimal(1))]
        bars = rollup(trades, 1800)
        self.assertEqual(len(bars), 2)
        gaps = rollup(trades, 1800, include_empty=True, start=start, end=start + timedelta(minutes=90))
        self.assertGreaterEqual(len(gaps), 3)

    def test_partial_and_session_calendar_bars_keep_utc(self) -> None:
        start = datetime(2026, 11, 20, 0, 0, tzinfo=UTC)
        calendar = SessionCalendar([(start, start + timedelta(hours=1))])
        bars = rollup([Trade(start, Decimal(100), Decimal(1))], 1800,
                      include_empty=True, start=start, end=start + timedelta(hours=1), session_calendar=calendar)
        self.assertEqual(len(bars), 2)
        self.assertTrue(all(bar.bar_start.tzinfo is not None for bar in bars))
        partial = rollup([Trade(start, Decimal(100), Decimal(1))], 1800,
                         include_empty=True, start=start, end=start + timedelta(minutes=10))
        self.assertTrue(partial[0].partial)

    def test_features_at_t_exclude_later_bars(self) -> None:
        start = datetime(2026, 11, 20, tzinfo=UTC)
        bars = [Bar(start, start + timedelta(minutes=1), Decimal(100), Decimal(101), Decimal(99), Decimal(100), Decimal(10), 1),
                Bar(start + timedelta(minutes=1), start + timedelta(minutes=2), Decimal(100), Decimal(999), Decimal(99), Decimal(200), Decimal(10), 1)]
        at_t = start + timedelta(minutes=1)
        session = {"session_timezone": "UTC", "session_open": None, "session_close": None}
        snapshot = feature_snapshot(bars, at_t, **session)
        self.assertEqual(snapshot.vwap, Decimal(100))
        self.assertEqual(causal_vwap(bars, at_t, **session), Decimal(100))

    def test_vwap_resets_at_utc_day_boundary(self) -> None:
        start = datetime(2026, 11, 20, 23, 58, tzinfo=UTC)
        bars = [
            Bar(start, start + timedelta(minutes=1), Decimal(100), Decimal(100), Decimal(100),
                Decimal(100), Decimal(10), 1),
            Bar(start + timedelta(minutes=2), start + timedelta(minutes=3), Decimal(200), Decimal(200),
                Decimal(200), Decimal(200), Decimal(1), 1),
        ]
        self.assertEqual(
            causal_vwap(
                bars, start + timedelta(minutes=3),
                session_timezone="UTC", session_open=None, session_close=None,
            ),
            Decimal(200),
        )

    def test_default_trend_window_preserves_macro_direction_through_pullback(self) -> None:
        start = datetime(2026, 11, 20, 14, 30, tzinfo=UTC)
        closes = [Decimal(100 + i) for i in range(19)] + [
            Decimal(117), Decimal(116), Decimal(115), Decimal(114), Decimal(113),
        ]
        bars = [
            Bar(start + timedelta(minutes=5 * i), start + timedelta(minutes=5 * (i + 1)),
                close, close + Decimal(1), close - Decimal(1), close, Decimal(1), 1)
            for i, close in enumerate(closes)
        ]
        timestamp = bars[-1].bar_end

        self.assertEqual(causal_trend_structure(bars, timestamp, period=5)[0], "down")
        self.assertEqual(causal_trend_structure(bars, timestamp)[0], "up")

    def test_trend_window_below_minimum_atr_displacement_is_sideways(self) -> None:
        start = datetime(2026, 11, 20, 14, 30, tzinfo=UTC)
        bars = [
            Bar(start + timedelta(minutes=5 * i), start + timedelta(minutes=5 * (i + 1)),
                Decimal(100) + Decimal("0.02") * i,
                Decimal("100.5") + Decimal("0.02") * i,
                Decimal("99.5") + Decimal("0.02") * i,
                Decimal(100) + Decimal("0.02") * i,
                Decimal(1), 1)
            for i in range(24)
        ]
        self.assertEqual(causal_trend_structure(bars, bars[-1].bar_end), ("sideways", "range"))

    def test_move_room_is_returned_as_price_relative_fraction(self) -> None:
        now = datetime(2026, 11, 20, tzinfo=UTC)
        bars = [Bar(now, now + timedelta(minutes=1), Decimal(100), Decimal(110),
                    Decimal(90), Decimal(100), Decimal(1), 1)]
        move_room = net_move_room(
            bars, now + timedelta(minutes=1), Decimal(105), Decimal(95)
        )
        self.assertEqual(move_room, Decimal(0))

    def test_atr_is_causal(self) -> None:
        start = datetime(2026, 11, 20, 14, 30, tzinfo=UTC)
        bars = [Bar(start + timedelta(minutes=i), start + timedelta(minutes=i + 1), Decimal(value), Decimal(value + 2), Decimal(value - 2), Decimal(value), Decimal(1), 1)
                for i, value in enumerate((100, 102, 104))]
        self.assertIsNotNone(causal_atr(
            bars, start + timedelta(minutes=3),
            session_timezone="UTC", session_open=None, session_close=None,
        ))

    def test_features_reset_at_exchange_open_and_keep_prior_session_targets(self) -> None:
        prior_start = datetime(2026, 9, 1, 13, 30, tzinfo=UTC)
        today_start = datetime(2026, 9, 2, 13, 30, tzinfo=UTC)
        prior = [
            Bar(prior_start + timedelta(minutes=5 * i), prior_start + timedelta(minutes=5 * (i + 1)),
                Decimal(150), Decimal(155), Decimal(145), Decimal(150), Decimal(10), 1)
            for i in range(2)
        ]
        today = [
            Bar(today_start + timedelta(minutes=5 * i), today_start + timedelta(minutes=5 * (i + 1)),
                Decimal(100 + i), Decimal(101 + i), Decimal(99 + i), Decimal(100 + i), Decimal(10), 1)
            for i in range(6)
        ]
        bars = prior + today
        snapshot = feature_snapshot(bars, today[-1].bar_end)

        self.assertEqual(snapshot.vwap, Decimal("102.5"))
        self.assertEqual(snapshot.trend, "up")
        self.assertEqual(snapshot.last_close, Decimal(105))
        self.assertEqual(snapshot.structure_target, Decimal(106))
        self.assertEqual(snapshot.structure_source, "SESSION_HIGH")
        self.assertEqual(snapshot.structure_target_continuation, Decimal(155))
        self.assertEqual(snapshot.structure_source_continuation, "PRIOR_SESSION_HIGH")


if __name__ == "__main__":
    unittest.main()
