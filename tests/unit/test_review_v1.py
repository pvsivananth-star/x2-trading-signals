"""Review v1: reaction-zone, confirmed-exit, and pre-close flatten contracts for Confluence_v1."""

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.backtest.bars import Bar
from app.features import FeatureSnapshot, causal_pullback_depth
from app.policy import TradingPolicy
from app.regime import Regime
from app.strategy import ConfluenceV1, classify_reaction_zone
from tests.unit.test_phase6_7 import MARKETS, STRATEGY, snapshot

NOW = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)


def _engine() -> ConfluenceV1:
    return ConfluenceV1(STRATEGY, TradingPolicy(MARKETS, "policy-test"))


def _snap(**overrides) -> FeatureSnapshot:
    values = {"context_timestamp": NOW}
    values.update(overrides)
    return replace(snapshot(), **values)


def _eval(engine, snap, **kwargs):
    base = {"timestamp": NOW, "symbol": "AAPL", "regime": Regime.TREND_DAY, "stale_feed_seconds": 1,
            "stale_threshold_seconds": 10, "minutes_to_close": 120}
    if (
        "previous_snapshot" not in kwargs
        and not kwargs.get("position_quantity", 0)
        and snap.trend == "up"
        and snap.last_close is not None
    ):
        base["previous_snapshot"] = replace(snap, last_close=snap.last_close - Decimal("0.1"))
    base.update(kwargs)
    return engine.evaluate(snapshot=snap, **base)


class ReactionZoneTests(unittest.TestCase):
    def test_zone_boundaries(self):
        self.assertEqual(classify_reaction_zone(Decimal("0.382")), "CORE_382_618")
        self.assertEqual(classify_reaction_zone(Decimal("0.618")), "CORE_382_618")
        self.assertEqual(classify_reaction_zone(Decimal("0.30")), "SHALLOW_VWAP_236_382")
        self.assertEqual(classify_reaction_zone(Decimal("0.70")), "DEEP_RECLAIM_618_786")
        self.assertIsNone(classify_reaction_zone(Decimal("0.10")))
        self.assertIsNone(classify_reaction_zone(Decimal("0.90")))
        self.assertIsNone(classify_reaction_zone(None))

    def test_entry_outside_any_zone_is_rejected(self):
        card = _eval(_engine(), _snap(pullback_depth=Decimal("0.95")))
        self.assertEqual(card.action, "hold")
        self.assertIn("outside_reaction_zone", card.rejected_gates)

    def test_shallow_zone_requires_vwap_hold(self):
        engine = _engine()
        below = _snap(pullback_depth=Decimal("0.30"), last_close=Decimal(99), vwap=Decimal(100))
        self.assertNotEqual(_eval(engine, below).action, "enter_long")
        held = _snap(pullback_depth=Decimal("0.30"), last_close=Decimal(101), vwap=Decimal(100))
        card = _eval(engine, held)
        self.assertEqual(card.action, "enter_long")
        self.assertEqual(card.parameters["zone"], "SHALLOW_VWAP_236_382")
        self.assertEqual(card.parameters["size_weight"], "0.68")

    def test_deep_zone_requires_reclaim_close(self):
        engine = _engine()
        no_reclaim = _snap(pullback_depth=Decimal("0.70"), last_close=Decimal(99), vwap=Decimal(100))
        self.assertNotEqual(_eval(engine, no_reclaim).action, "enter_long")
        reclaim = _snap(pullback_depth=Decimal("0.70"), last_close=Decimal("100.5"), vwap=Decimal(100))
        self.assertEqual(_eval(engine, reclaim).parameters["zone"], "DEEP_RECLAIM_618_786")

    def test_core_zone_size_weight_is_full(self):
        card = _eval(_engine(), _snap())
        self.assertEqual(card.action, "enter_long")
        self.assertEqual(card.parameters["size_weight"], "1")

    def test_missing_close_fails_closed(self):
        card = _eval(_engine(), _snap(last_close=None, pullback_depth=Decimal("0.30")))
        self.assertNotEqual(card.action, "enter_long")

    def test_entry_requires_fresh_completed_30m_context(self):
        card = _eval(_engine(), _snap(context_regime=None, context_timestamp=None))
        self.assertNotEqual(card.action, "enter_long")
        self.assertIn("30m_context_unavailable_or_stale", card.rejected_gates)

    def test_entry_requires_confirmed_aligned_15m_swing(self):
        card = _eval(_engine(), _snap(swing_15m_direction="down"))
        self.assertNotEqual(card.action, "enter_long")
        self.assertIn("15m_swing_unconfirmed_or_misaligned", card.rejected_gates)


class ExitContractTests(unittest.TestCase):
    def _down(self):
        return _snap(trend="down", structure="lower-highs-lower-lows", move_room=Decimal(-2), vwap=Decimal(101))

    def test_single_bar_break_does_not_exit(self):
        card = _eval(_engine(), self._down(), position_quantity=Decimal(1))
        self.assertNotEqual(card.action, "exit_long")

    def test_confirmed_break_exits(self):
        card = _eval(_engine(), self._down(), position_quantity=Decimal(1),
                     previous_snapshot=self._down())
        self.assertEqual(card.action, "exit_long")
        self.assertEqual(card.display_action, "SELL")

    def test_pre_close_flatten_forces_exit_while_long(self):
        card = _eval(_engine(), snapshot(), position_quantity=Decimal(1), minutes_to_close=10)
        self.assertEqual(card.action, "exit_long")
        self.assertEqual(card.selected_fib_zone, "MANDATORY_PRE_CLOSE_FLAT")
        self.assertIn("mandatory pre-close flat", card.reason)

    def test_pre_close_blocks_new_entry(self):
        card = _eval(_engine(), snapshot(), minutes_to_close=30)
        self.assertNotEqual(card.action, "enter_long")
        self.assertIn("entry_cutoff", card.rejected_gates)


class PullbackDepthTests(unittest.TestCase):
    def test_depth_ignores_bars_after_timestamp(self):
        start = datetime(2026, 11, 20, 14, 30, tzinfo=UTC)
        bars = [Bar(start + timedelta(minutes=i), start + timedelta(minutes=i + 1),
                    Decimal(100), Decimal(110), Decimal(90), Decimal(100), Decimal(1), 1)
                for i in range(5)]
        cutoff = bars[2].bar_end
        future = bars[:3] + [Bar(bars[3].bar_start, bars[3].bar_end, Decimal(100), Decimal(500),
                                 Decimal(1), Decimal(1), Decimal(1), 1)] + bars[4:]
        self.assertEqual(causal_pullback_depth(future, cutoff), causal_pullback_depth(bars, cutoff))


if __name__ == "__main__":
    unittest.main()
