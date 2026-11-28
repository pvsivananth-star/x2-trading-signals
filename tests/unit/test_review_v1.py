"""Review v1: reaction-zone, confirmed-exit, and pre-close flatten contracts for Confluence_v1."""

import unittest
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.backtest.bars import Bar
from app.event_risk import EventRiskAssessment
from app.features import FeatureSnapshot, causal_pullback_depth, causal_slot_rvol
from app.policy import TradingPolicy
from app.regime import Regime
from app.strategy import ConfluenceV1, classify_reaction_zone
from tests.unit.test_phase6_7 import MARKETS, STRATEGY, snapshot

NOW = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)


def _engine() -> ConfluenceV1:
    return ConfluenceV1(deepcopy(STRATEGY), TradingPolicy(MARKETS, "policy-test"))


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


    def test_move_room_uses_market_specific_cost_estimate_and_reports_it(self):
        markets = deepcopy(MARKETS)
        markets["markets"]["US_EQUITIES"]["est_roundtrip_cost_pct"] = 0.0025
        engine = ConfluenceV1(deepcopy(STRATEGY), TradingPolicy(markets, "market-cost-test"))
        card = _eval(engine, _snap())
        self.assertEqual(card.parameters["roundtrip_cost_estimate_pct"], "0.0025")
        self.assertEqual(card.parameters["slippage_buffer_pct"], "0.0005")

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

    def test_impulse_volume_does_not_bypass_baseline_rvol_floor(self):
        card = _eval(_engine(), _snap(
            rvol=Decimal("1.00"), impulse_rvol=Decimal("1.80")
        ))
        self.assertNotEqual(card.action, "enter_long")
        self.assertIn("rvol_below_threshold", card.rejected_gates)

    def test_impulse_volume_still_requires_pullback_dry_up(self):
        card = _eval(_engine(), _snap(
            rvol=Decimal("1.30"), impulse_rvol=Decimal("1.80")
        ))
        self.assertNotEqual(card.action, "enter_long")
        self.assertIn("dry_up_not_met", card.rejected_gates)

    def test_required_event_gate_fails_closed_without_assessment(self):
        engine = _engine()
        engine.strategy["event_risk"] = {"required": True, "max_age_seconds": 900}
        card = _eval(engine, _snap())
        self.assertNotEqual(card.action, "enter_long")
        self.assertIn("event_risk_unavailable_or_stale", card.rejected_gates)
        self.assertEqual(card.inputs["event_risk_state"], "unavailable")

    def test_required_event_gate_accepts_fresh_clear_assessment(self):
        engine = _engine()
        engine.strategy["event_risk"] = {"required": True, "max_age_seconds": 900}
        assessment = EventRiskAssessment("clear", NOW, "no high-impact events in block window", "test-calendar")
        card = _eval(engine, _snap(), event_risk=assessment)
        self.assertEqual(card.action, "enter_long")
        self.assertEqual(card.inputs["event_risk_state"], "clear")

    def test_required_event_gate_rejects_blocked_and_stale_assessments(self):
        engine = _engine()
        engine.strategy["event_risk"] = {"required": True, "max_age_seconds": 900}
        blocked = EventRiskAssessment("blocked", NOW, "high-impact event nearby", "test-calendar")
        blocked_card = _eval(engine, _snap(), event_risk=blocked)
        self.assertIn("event_risk_blocked", blocked_card.rejected_gates)
        stale = EventRiskAssessment("clear", NOW - timedelta(minutes=20), "stale feed", "test-calendar")
        stale_card = _eval(engine, _snap(), event_risk=stale)
        self.assertIn("event_risk_unavailable_or_stale", stale_card.rejected_gates)

    def test_chop_playbook_rejects_trend_continuation_core_zone(self):
        engine = _engine()
        engine.strategy["playbooks"] = {
            "CHOP_DAY": {"allowed_reaction_zones": ["DEEP_RECLAIM_618_786"]}
        }
        card = _eval(engine, _snap(context_regime="CHOP_DAY", pullback_depth=Decimal("0.50")),
                     regime=Regime.CHOP_DAY)
        self.assertNotEqual(card.action, "enter_long")
        self.assertIn("regime_playbook_zone_not_allowed", card.rejected_gates)

    def test_chop_playbook_allows_range_edge_reclaim(self):
        engine = _engine()
        engine.strategy["playbooks"] = {
            "CHOP_DAY": {"allowed_reaction_zones": ["DEEP_RECLAIM_618_786"]}
        }
        card = _eval(engine, _snap(context_regime="CHOP_DAY", pullback_depth=Decimal("0.70"),
                                  last_close=Decimal("100.5"), vwap=Decimal(100)),
                     regime=Regime.CHOP_DAY)
        self.assertEqual(card.action, "enter_long")
        self.assertEqual(card.parameters["zone"], "DEEP_RECLAIM_618_786")

    def test_trend_day_short_requires_bearish_close_confirmation(self):
        snap = _snap(trend="down", structure="lower-highs-lower-lows",
                     context_regime="TREND_DAY", pullback_depth_short=Decimal("0.50"),
                     last_close=Decimal(99), vwap=Decimal(100),
                     swing_15m_direction="down")
        card = _eval(_engine(), snap, regime=Regime.TREND_DAY)
        self.assertNotEqual(card.action, "enter_short")
        self.assertIn("directional_close_confirmation_not_met", card.rejected_gates)

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


class SlotRvolTests(unittest.TestCase):
    def test_same_time_slot_uses_prior_sessions_only(self):
        start_dates = [datetime(2026, 11, day, 14, 30, tzinfo=UTC) for day in (16, 17, 18, 19, 20, 23)]
        volumes = [Decimal(100), Decimal(200), Decimal(100), Decimal(200), Decimal(100), Decimal(280)]
        bars = [Bar(start, start + timedelta(minutes=5), Decimal(100), Decimal(101),
                    Decimal(99), Decimal(100), volume, 1)
                for start, volume in zip(start_dates, volumes)]
        value, samples = causal_slot_rvol(bars, bars[-1].bar_end, min_sessions=5)
        self.assertEqual(samples, 5)
        self.assertEqual(value, Decimal(2))

    def test_slot_rvol_fails_closed_with_insufficient_prior_sessions(self):
        start = datetime(2026, 11, 20, 14, 30, tzinfo=UTC)
        bars = [Bar(start, start + timedelta(minutes=5), Decimal(100), Decimal(101),
                    Decimal(99), Decimal(100), Decimal(100), 1)]
        value, samples = causal_slot_rvol(bars, bars[-1].bar_end, min_sessions=5)
        self.assertIsNone(value)
        self.assertEqual(samples, 0)

    def test_slot_rvol_ignores_future_bars(self):
        starts = [datetime(2026, 11, day, 14, 30, tzinfo=UTC) for day in (16, 17, 18, 19, 20, 23)]
        bars = [Bar(start, start + timedelta(minutes=5), Decimal(100), Decimal(101),
                    Decimal(99), Decimal(100), Decimal(100), 1) for start in starts]
        cutoff = bars[-1].bar_end
        future = Bar(cutoff, cutoff + timedelta(minutes=5), Decimal(100), Decimal(101),
                     Decimal(99), Decimal(100), Decimal(10000), 1)
        self.assertEqual(causal_slot_rvol(bars + [future], cutoff), causal_slot_rvol(bars, cutoff))


    def test_feature_snapshot_exposes_slot_and_rolling_rvol_separately(self):
        from app.features import feature_snapshot
        starts = [datetime(2026, 11, day, 14, 30, tzinfo=UTC) for day in (16, 17, 18, 19, 20, 23)]
        volumes = [Decimal(100), Decimal(200), Decimal(100), Decimal(200), Decimal(100), Decimal(280)]
        bars = [Bar(start, start + timedelta(minutes=5), Decimal(100), Decimal(101),
                    Decimal(99), Decimal(100), volume, 1)
                for start, volume in zip(starts, volumes)]
        snap = feature_snapshot(bars, bars[-1].bar_end)
        self.assertEqual(snap.rvol, Decimal(2))
        self.assertEqual(snap.slot_rvol_samples, 5)
        self.assertIsNotNone(snap.rolling_rvol)


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
