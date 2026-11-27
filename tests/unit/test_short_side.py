"""Short-side contract for Confluence_v1 (mirror of the long rules)."""

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.backtest.bars import Bar
from app.features import causal_structure_target_short
from app.policy import TradingPolicy
from app.regime import Regime
from app.strategy import ConfluenceV1
from tests.unit.test_phase6_7 import MARKETS, STRATEGY, snapshot

NOW = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)


def _short(**kw):
    base = replace(snapshot(), trend="down", structure="lower-highs-lower-lows",
                   pullback_depth=None, vwap=Decimal(100), last_close=Decimal(100), atr=Decimal(2),
                   pullback_depth_short=Decimal("0.5"), structure_target_short=Decimal(95),
                   structure_source_short="SWING_LOW", rvol=Decimal("1.2"))
    return replace(base, **kw)


def _eval(snap, **kw):
    engine = ConfluenceV1(STRATEGY, TradingPolicy(MARKETS, "policy-test"))
    args = {"timestamp": NOW, "symbol": "AAPL", "snapshot": snap, "regime": Regime.TREND_DAY,
            "stale_feed_seconds": 1, "stale_threshold_seconds": 10, "minutes_to_close": 120}
    args.update(kw)
    return engine.evaluate(**args)


class ShortEntryTests(unittest.TestCase):
    def test_short_entry_on_downtrend_pullback_to_structure(self):
        card = _eval(_short())
        self.assertEqual(card.action, "enter_short")
        self.assertEqual(card.display_action, "SHORT")
        self.assertEqual(card.parameters["side"], "short")
        self.assertEqual(card.parameters["target_source"], "SWING_LOW")

    def test_short_target_is_structure_below_entry(self):
        card = _eval(_short())
        self.assertEqual(card.parameters["target"], "95")

    def test_short_shallow_zone_needs_vwap_rejection(self):
        held = _short(pullback_depth_short=Decimal("0.30"), last_close=Decimal(99), vwap=Decimal(100))
        self.assertEqual(_eval(held).action, "enter_short")
        rejected = _short(pullback_depth_short=Decimal("0.30"), last_close=Decimal(101), vwap=Decimal(100))
        card = _eval(rejected)
        self.assertNotEqual(card.action, "enter_short")
        self.assertIn("vwap_hold_not_met", card.rejected_gates)

    def test_short_without_structure_uses_atr_open_space_below(self):
        card = _eval(_short(structure_target_short=None, structure_source_short=None))
        self.assertEqual(card.parameters["target"], "96")  # 100 - 2.0 * 2 (test config target_atr_mult)
        self.assertEqual(card.parameters["target_source"], "ATR_OPEN_SPACE")

    def test_short_structure_beyond_cap_is_capped(self):
        card = _eval(_short(structure_target_short=Decimal(90)))
        self.assertEqual(card.parameters["target"], "92")  # capped at 100 - 4 * 2

    def test_short_blocked_when_structure_too_close(self):
        card = _eval(_short(structure_target_short=Decimal("99.5")))
        self.assertNotEqual(card.action, "enter_short")
        self.assertIn("insufficient_reward_to_risk", card.rejected_gates)

    def test_no_short_in_uptrend(self):
        card = _eval(replace(_short(), trend="up", structure="higher-highs-higher-lows"))
        self.assertNotEqual(card.action, "enter_short")

    def test_short_side_can_be_disabled_by_config(self):
        cfg = {**STRATEGY, "strategy": {**STRATEGY["strategy"], "sides": ["long"]}}
        engine = ConfluenceV1(cfg, TradingPolicy(MARKETS, "policy-test"))
        card = engine.evaluate(timestamp=NOW, symbol="AAPL", snapshot=_short(), regime=Regime.TREND_DAY,
                               stale_feed_seconds=1, stale_threshold_seconds=10, minutes_to_close=120)
        self.assertNotEqual(card.action, "enter_short")


class ShortExitTests(unittest.TestCase):
    def test_hard_stop_above_entry_exits_short(self):
        card = _eval(_short(last_close=Decimal(104)), position_quantity=Decimal(-1), position_stop=Decimal(103))
        self.assertEqual(card.action, "exit_short")
        self.assertEqual(card.display_action, "COVER")

    def test_target_below_exits_short(self):
        card = _eval(_short(last_close=Decimal(94)), position_quantity=Decimal(-1), position_target=Decimal(95))
        self.assertEqual(card.action, "exit_short")
        self.assertEqual(card.selected_fib_zone, "TARGET_HIT")

    def test_single_bar_up_move_does_not_exit_short(self):
        card = _eval(replace(_short(), trend="up", structure="higher-highs-higher-lows"),
                     position_quantity=Decimal(-1))
        self.assertNotEqual(card.action, "exit_short")

    def test_confirmed_up_break_exits_short(self):
        up = replace(_short(), trend="up", structure="higher-highs-higher-lows")
        card = _eval(up, position_quantity=Decimal(-1), previous_snapshot=up)
        self.assertEqual(card.action, "exit_short")

    def test_pre_close_flat_covers_short(self):
        card = _eval(_short(), position_quantity=Decimal(-1), minutes_to_close=10)
        self.assertEqual(card.action, "exit_short")
        self.assertEqual(card.selected_fib_zone, "MANDATORY_PRE_CLOSE_FLAT")


class ShortFeatureTests(unittest.TestCase):
    def _bars(self, day, lows):
        start = datetime(2026, 11, day, 14, 30, tzinfo=UTC)
        return [Bar(start + timedelta(minutes=5 * i), start + timedelta(minutes=5 * (i + 1)),
                    Decimal(100), Decimal(101), Decimal(lo), Decimal(100), Decimal(1), 1)
                for i, lo in enumerate(lows)]

    def test_nearest_structure_below_close_wins(self):
        bars = self._bars(20, [100, 99, 97, 98])
        target, _source = causal_structure_target_short(bars, bars[-1].bar_end)
        self.assertEqual(target, Decimal(97))

    def test_prior_session_low_is_candidate(self):
        prior = self._bars(19, [90, 91, 92])
        today = self._bars(20, [100, 100, 100])
        bars = prior + today
        target, source = causal_structure_target_short(bars, bars[-1].bar_end)
        self.assertEqual(target, Decimal(90))
        self.assertEqual(source, "PRIOR_SESSION_LOW")

    def test_no_support_below_close_returns_none(self):
        bars = self._bars(20, [100, 100, 100])
        self.assertEqual(causal_structure_target_short(bars, bars[-1].bar_end), (None, None))


if __name__ == "__main__":
    unittest.main()
