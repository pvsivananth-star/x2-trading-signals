"""Upgrades: volume pump/dry-up entry, momentum zone on TREND_DAY, volume climax exit, archetype multipliers."""

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import yaml

from app.backtest.bars import Bar
from app.features import causal_impulse_rvol
from app.policy import TradingPolicy
from app.regime import Regime
from app.strategy import ConfluenceV1, classify_reaction_zone
from tests.unit.test_phase6_7 import MARKETS, STRATEGY, snapshot

NOW = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]


def _engine(markets=MARKETS):
    return ConfluenceV1(STRATEGY, TradingPolicy(markets, "policy-test"))


def _eval(snap, symbol="AAPL", engine=None, **kw):
    snap = replace(snap, context_timestamp=NOW)
    if kw.get("previous_snapshot") is not None:
        kw["previous_snapshot"] = replace(kw["previous_snapshot"], context_timestamp=NOW)
    args = {"timestamp": NOW, "symbol": symbol, "snapshot": snap, "regime": Regime.TREND_DAY,
            "stale_feed_seconds": 1, "stale_threshold_seconds": 10, "minutes_to_close": 120}
    args.update(kw)
    return (engine or _engine()).evaluate(**args)


def _prev_close_below():
    """Previous bar closed lower, so the TREND_DAY bullish-close rule is satisfied."""
    return replace(snapshot(), last_close=Decimal("99.5"))


def _bar(i, volume, close=100):
    start = datetime(2026, 11, 20, 14, 30, tzinfo=UTC) + timedelta(minutes=5 * i)
    return Bar(start, start + timedelta(minutes=5), Decimal(close), Decimal(close + 1),
               Decimal(close - 1), Decimal(close), Decimal(volume), 1)


class MomentumZoneTests(unittest.TestCase):
    def test_momentum_zone_only_on_trend_day_with_impulse(self):
        self.assertEqual(classify_reaction_zone(Decimal("0.20"), Regime.TREND_DAY, Decimal("1.5")),
                         "MOMENTUM_VWAP_12_236")
        self.assertIsNone(classify_reaction_zone(Decimal("0.20"), Regime.CHOP_DAY, Decimal("1.5")))
        self.assertIsNone(classify_reaction_zone(Decimal("0.20"), Regime.TREND_DAY, Decimal("1.0")))
        self.assertIsNone(classify_reaction_zone(Decimal("0.20"), Regime.TREND_DAY, None))

    def test_existing_zone_boundaries_unchanged(self):
        self.assertEqual(classify_reaction_zone(Decimal("0.30")), "SHALLOW_VWAP_236_382")
        self.assertEqual(classify_reaction_zone(Decimal("0.50")), "CORE_382_618")
        self.assertIsNone(classify_reaction_zone(Decimal("0.10")))

    def test_momentum_entry_needs_vwap_hold_and_one_bar_hook(self):
        snap = replace(snapshot(), pullback_depth=Decimal("0.20"), impulse_rvol=Decimal("1.5"),
                       last_close=Decimal("100.5"), vwap=Decimal(100), rvol=Decimal("1.0"))
        prev_up = replace(snap, last_close=Decimal("100.2"))
        card = _eval(snap, previous_snapshot=prev_up)
        self.assertEqual(card.action, "enter_long")
        self.assertEqual(card.parameters["zone"], "MOMENTUM_VWAP_12_236")
        self.assertEqual(card.parameters["size_weight"], "0.75")
        no_hook = _eval(snap, previous_snapshot=replace(snap, last_close=Decimal("100.8")))
        self.assertNotEqual(no_hook.action, "enter_long")
        self.assertIn("momentum_vwap_hold_not_met", no_hook.rejected_gates)


class VolumePumpDryUpTests(unittest.TestCase):
    def test_pump_and_dry_up_pass(self):
        snap = replace(snapshot(), impulse_rvol=Decimal("1.5"), rvol=Decimal("1.0"))
        self.assertEqual(_eval(snap, previous_snapshot=_prev_close_below()).action, "enter_long")

    def test_weak_impulse_is_rejected(self):
        snap = replace(snapshot(), impulse_rvol=Decimal("1.0"), rvol=Decimal("1.0"))
        card = _eval(snap)
        self.assertNotEqual(card.action, "enter_long")
        self.assertIn("impulse_volume_not_met", card.rejected_gates)

    def test_pullback_bar_must_dry_up(self):
        snap = replace(snapshot(), impulse_rvol=Decimal("1.5"), rvol=Decimal("1.3"))
        card = _eval(snap)
        self.assertNotEqual(card.action, "enter_long")
        self.assertIn("dry_up_not_met", card.rejected_gates)

    def test_falls_back_to_old_rvol_rule_when_impulse_unknown(self):
        self.assertEqual(_eval(replace(snapshot(), impulse_rvol=None, rvol=Decimal("1.2")),
                               previous_snapshot=_prev_close_below()).action, "enter_long")
        self.assertIn("rvol_below_threshold",
                      _eval(replace(snapshot(), impulse_rvol=None, rvol=Decimal("0.5"))).rejected_gates)

    def test_impulse_counts_prior_bars_not_current_bar(self):
        base = [_bar(i, 100) for i in range(25)]
        spike_prior = base[:20] + [_bar(20, 300)] + base[21:24] + [_bar(24, 100)]
        spike_now = base[:24] + [_bar(24, 300)]
        self.assertGreaterEqual(causal_impulse_rvol(spike_prior, spike_prior[-1].bar_end), Decimal("1.4"))
        self.assertLess(causal_impulse_rvol(spike_now, spike_now[-1].bar_end), Decimal("1.4"))

    def test_impulse_ignores_bars_after_timestamp(self):
        bars = [_bar(i, 100) for i in range(25)]
        future = bars[:22] + [replace(bars[22], volume=Decimal(10000))] + bars[23:]
        cutoff = bars[21].bar_end
        self.assertEqual(causal_impulse_rvol(future, cutoff), causal_impulse_rvol(bars, cutoff))


class VolumeClimaxExitTests(unittest.TestCase):
    def _long_position(self, **kw):
        return replace(snapshot(), trend="up", structure="higher-highs-higher-lows", **kw)

    def test_single_bar_climax_exits_long(self):
        prev = self._long_position(last_close=Decimal("100.5"))
        snap = self._long_position(last_close=Decimal(99), vwap=Decimal(100), rvol=Decimal("2.5"))
        card = _eval(snap, position_quantity=Decimal(1), previous_snapshot=prev)
        self.assertEqual(card.action, "exit_long")
        self.assertEqual(card.selected_fib_zone, "VOLUME_CLIMAX_EXIT")

    def test_no_climax_without_volume_or_vwap_cross(self):
        prev = self._long_position(last_close=Decimal("100.5"))
        low_vol = self._long_position(last_close=Decimal(99), vwap=Decimal(100), rvol=Decimal("1.5"))
        self.assertNotEqual(_eval(low_vol, position_quantity=Decimal(1), previous_snapshot=prev).action, "exit_long")
        above_vwap = self._long_position(last_close=Decimal(101), vwap=Decimal(100), rvol=Decimal("2.5"))
        self.assertNotEqual(_eval(above_vwap, position_quantity=Decimal(1), previous_snapshot=prev).action, "exit_long")


class ArchetypeMultiplierTests(unittest.TestCase):
    def _markets(self):
        import copy
        markets = copy.deepcopy(MARKETS)
        markets["markets"]["US_EQUITIES"].update(stop_mult=1.0, target_mult=3.5)
        markets["markets"]["CRYPTO_MAJORS"].update(stop_mult=1.4, target_mult=4.0)
        return markets

    def test_equity_archetype_multipliers_on_card(self):
        card = _eval(replace(snapshot(), impulse_rvol=None), engine=_engine(self._markets()))
        self.assertEqual(card.parameters["stop_mult"], "1")
        self.assertEqual(card.parameters["target_mult"], "3.5")

    def test_crypto_archetype_multipliers_on_card(self):
        card = _eval(replace(snapshot(), impulse_rvol=None), symbol="BTC-USD", engine=_engine(self._markets()))
        self.assertEqual(card.parameters["stop_mult"], "1.4")
        self.assertEqual(card.parameters["target_mult"], "4")

    def test_shipped_config_carries_archetype_multipliers(self):
        cfg = yaml.safe_load((ROOT / "config" / "markets_17.yaml").read_text())["markets"]
        self.assertEqual((cfg["US_EQUITIES"]["stop_mult"], cfg["US_EQUITIES"]["target_mult"]), (1.0, 3.5))
        self.assertEqual((cfg["US_COMMODITIES"]["stop_mult"], cfg["US_COMMODITIES"]["target_mult"]), (1.1, 3.5))
        self.assertEqual((cfg["CRYPTO_MAJORS"]["stop_mult"], cfg["CRYPTO_MAJORS"]["target_mult"]), (1.4, 4.0))
        self.assertEqual((cfg["INDIA_NSE"]["stop_mult"], cfg["INDIA_NSE"]["target_mult"]), (1.0, 3.5))


if __name__ == "__main__":
    unittest.main()
