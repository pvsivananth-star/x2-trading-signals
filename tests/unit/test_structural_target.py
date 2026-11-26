"""Review v3: structural-primary target with ATR open-space fallback and reward-to-risk gate."""

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.backtest.bars import Bar
from app.features import causal_structure_target
from app.policy import TradingPolicy
from app.regime import Regime
from app.strategy import ConfluenceV1, projected_levels
from tests.unit.test_phase6_7 import MARKETS, STRATEGY, snapshot

NOW = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)


def _snap(**kw):
    # entry 100, ATR 2 -> 3.5 ATR = 107, 4 ATR = 108
    return replace(snapshot(), atr=Decimal(2), last_close=Decimal(100), **kw)


def _levels(snap):
    return projected_levels(snap, Decimal("3.5"), Decimal("4.0"), Decimal("1.0"),
                            Decimal("0.0010"), Decimal("0.0005"))


class StructuralTargetTests(unittest.TestCase):
    def test_structure_within_open_space_is_primary_target(self):
        lv = _levels(_snap(structure_target=Decimal(103), structure_source="SWING_HIGH"))
        self.assertEqual(lv.target, Decimal(103))
        self.assertEqual(lv.target_source, "SWING_HIGH")

    def test_no_structure_uses_atr_open_space_target(self):
        lv = _levels(_snap(structure_target=None))
        self.assertEqual(lv.target, Decimal(107))
        self.assertEqual(lv.target_source, "ATR_OPEN_SPACE")

    def test_structure_beyond_open_space_extends_only_to_cap(self):
        far = _levels(_snap(structure_target=Decimal(109), structure_source="PRIOR_SESSION_HIGH"))
        self.assertEqual(far.target, Decimal(108))  # capped at 4 ATR
        mid = _levels(_snap(structure_target=Decimal("107.5"), structure_source="SESSION_HIGH"))
        self.assertEqual(mid.target, Decimal("107.5"))  # structure between 3.5 and 4 ATR used directly

    def test_close_structure_is_not_overridden_by_atr(self):
        lv = _levels(_snap(structure_target=Decimal(101), structure_source="SWING_HIGH"))
        self.assertEqual(lv.target, Decimal(101))  # never widened to 3.5 ATR
        self.assertLess(lv.reward_to_risk, Decimal("1.5"))

    def test_blocked_by_close_structure_holds_and_does_not_fallback(self):
        engine = ConfluenceV1(STRATEGY, TradingPolicy(MARKETS, "policy-test"))
        card = engine.evaluate(timestamp=NOW, symbol="AAPL",
                               snapshot=_snap(structure_target=Decimal(101), structure_source="SWING_HIGH",
                                              pullback_depth=Decimal("0.5"), rvol=Decimal("1.2")),
                               regime=Regime.TREND_DAY, stale_feed_seconds=1,
                               stale_threshold_seconds=10, minutes_to_close=120)
        self.assertEqual(card.action, "hold")
        self.assertIn("insufficient_reward_to_risk", card.rejected_gates)
        self.assertEqual(card.parameters["zone"], "CORE_382_618")

    def test_reward_to_risk_gate_passes_with_open_space(self):
        engine = ConfluenceV1(STRATEGY, TradingPolicy(MARKETS, "policy-test"))
        card = engine.evaluate(timestamp=NOW, symbol="AAPL", snapshot=_snap(structure_target=None,
                               pullback_depth=Decimal("0.5"), rvol=Decimal("1.2")),
                               regime=Regime.TREND_DAY, stale_feed_seconds=1,
                               stale_threshold_seconds=10, minutes_to_close=120)
        self.assertEqual(card.action, "enter_long")
        self.assertNotIn("insufficient_reward_to_risk", card.rejected_gates)


class StructureDetectionTests(unittest.TestCase):
    def _bars(self, day, highs):
        start = datetime(2026, 11, day, 14, 30, tzinfo=UTC)
        return [Bar(start + timedelta(minutes=5 * i), start + timedelta(minutes=5 * (i + 1)),
                    Decimal(100), Decimal(h), Decimal(99), Decimal(100), Decimal(1), 1)
                for i, h in enumerate(highs)]

    def test_prior_session_high_is_candidate(self):
        prior = self._bars(19, [110, 111, 112])
        today = self._bars(20, [100, 100, 100])
        bars = prior + today
        target, source = causal_structure_target(bars, bars[-1].bar_end)
        self.assertEqual(target, Decimal(112))
        self.assertEqual(source, "PRIOR_SESSION_HIGH")

    def test_nearest_structure_above_close_wins(self):
        bars = self._bars(20, [100, 101, 103, 102])
        target, _source = causal_structure_target(bars, bars[-1].bar_end)
        self.assertEqual(target, Decimal(103))

    def test_no_structure_above_close_returns_none(self):
        bars = self._bars(20, [100, 100, 100])
        self.assertEqual(causal_structure_target(bars, bars[-1].bar_end), (None, None))

    def test_structure_ignores_bars_after_timestamp(self):
        bars = self._bars(20, [100, 101, 102])
        future = bars[:2] + [replace(bars[2], high=Decimal(500))]
        self.assertEqual(causal_structure_target(future, bars[1].bar_end),
                         causal_structure_target(bars, bars[1].bar_end))


if __name__ == "__main__":
    unittest.main()
