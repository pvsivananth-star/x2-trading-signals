"""Dataset backtest runner: session timing, cost rules, intrabar exits, and end-to-end invariants."""

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.backtest.bars import Bar
from app.backtest.simulator import Fill, OrderSimulator, SimulationConfig
from app.backtest.dataset_runner import (
    COST_SECTION,
    DEFAULT_PROFILE,
    Position,
    Trade,
    _commission,
    _cost_model,
    _intrabar_exit,
    causal_regime,
    entry_costs,
    exit_costs,
    minutes_to_close,
    run_dataset,
    run_symbol,
    summarize,
)
from app.costs import load_costs
from app.policy import TradingPolicy
from app.regime import Regime

FIXTURE = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "synthetic_ci"


def _bar(minute: int, o: str, h: str, l: str, c: str) -> Bar:
    start = datetime(2026, 11, 2, 14, 30, tzinfo=UTC) + timedelta(minutes=minute)
    return Bar(start, start + timedelta(minutes=1), Decimal(o), Decimal(h), Decimal(l), Decimal(c), Decimal(100), 1)


class SessionTimingTests(unittest.TestCase):
    def test_equity_minutes_to_close(self):
        self.assertEqual(minutes_to_close(datetime(2026, 11, 2, 20, 45, tzinfo=UTC), "US_EQUITIES"), 15)
        self.assertEqual(minutes_to_close(datetime(2026, 11, 2, 21, 0, tzinfo=UTC), "US_EQUITIES"), 0)

    def test_equity_minutes_to_close_tracks_dst(self):
        self.assertEqual(minutes_to_close(datetime(2026, 9, 2, 19, 45, tzinfo=UTC), "US_EQUITIES"), 15)
        self.assertEqual(minutes_to_close(datetime(2026, 9, 2, 20, 0, tzinfo=UTC), "US_EQUITIES"), 0)

    def test_commodity_minutes_to_close(self):
        self.assertEqual(minutes_to_close(datetime(2026, 11, 2, 20, 45, tzinfo=UTC), "US_COMMODITIES"), 15)

    def test_crypto_has_no_session_clock(self):
        self.assertIsNone(minutes_to_close(datetime(2026, 11, 2, 20, 45, tzinfo=UTC), "CRYPTO_MAJORS"))

    def test_unknown_profile_uses_default_session_clock(self):
        self.assertEqual(minutes_to_close(datetime(2026, 11, 2, 20, 45, tzinfo=UTC), "UNKNOWN"), 15)

    def test_default_profile_includes_requested_instruments(self):
        self.assertEqual(
            {symbol: DEFAULT_PROFILE[symbol] for symbol in ("SPY", "GLD", "BTC-USD")},
            {"SPY": "US_EQUITIES", "GLD": "US_COMMODITIES", "BTC-USD": "CRYPTO_MAJORS"},
        )
        self.assertEqual(COST_SECTION["US_COMMODITIES"], "US_ETF")

    def test_regime_uses_current_session_displacement_after_six_bars(self):
        start = datetime(2026, 11, 2, 14, 30, tzinfo=UTC)
        flat = [
            Bar(start + timedelta(minutes=i), start + timedelta(minutes=i + 1),
                Decimal(100), Decimal("100.5"), Decimal("99.5"), Decimal(100),
                Decimal(100), 1)
            for i in range(24)
        ]
        trending = [
            Bar(start + timedelta(minutes=i), start + timedelta(minutes=i + 1),
                Decimal(100 + i), Decimal(100 + i) + Decimal("0.5"),
                Decimal(100 + i) - Decimal("0.5"), Decimal(100 + i), Decimal(100), 1)
            for i in range(24)
        ]
        self.assertEqual(causal_regime(flat, flat[-1].bar_end), Regime.CHOP_DAY)
        self.assertEqual(causal_regime(trending, trending[-1].bar_end), Regime.TREND_DAY)
        self.assertEqual(causal_regime(trending, trending[0].bar_end), Regime.CHOP_DAY)
        self.assertEqual(causal_regime(trending[:5], trending[4].bar_end), Regime.CHOP_DAY)

    def test_regime_rejects_directionless_range_spike_and_detects_reversal_leg(self):
        start = datetime(2026, 11, 2, 14, 30, tzinfo=UTC)
        whipsaw_flat = [
            Bar(start + timedelta(minutes=i), start + timedelta(minutes=i + 1),
                Decimal(100), Decimal("100.5"), Decimal("99.5"), Decimal(100), Decimal(100), 1)
            for i in range(5)
        ]
        whipsaw_flat.append(Bar(start + timedelta(minutes=5), start + timedelta(minutes=6),
                                Decimal(100), Decimal(110), Decimal(90), Decimal(100), Decimal(100), 1))
        self.assertEqual(causal_regime(whipsaw_flat, whipsaw_flat[-1].bar_end), Regime.CHOP_DAY)

        reversal = whipsaw_flat + [
            Bar(start + timedelta(minutes=6 + i), start + timedelta(minutes=7 + i),
                Decimal(95 + i), Decimal(96 + i), Decimal(94 + i), Decimal(96 + i), Decimal(100), 1)
            for i in range(6)
        ]
        self.assertEqual(causal_regime(reversal, reversal[-1].bar_end), Regime.TREND_DAY)

    def test_regime_recognizes_intraday_range_expansion_without_net_displacement(self):
        start = datetime(2026, 11, 2, 14, 30, tzinfo=UTC)
        bars = [
            Bar(
                start + timedelta(minutes=i),
                start + timedelta(minutes=i + 1),
                Decimal(100),
                Decimal("100.5"),
                Decimal("99.5"),
                Decimal(100),
                Decimal(100),
                1,
            )
            for i in range(5)
        ]
        bars.append(Bar(start + timedelta(minutes=5), start + timedelta(minutes=6),
                        Decimal(100), Decimal(110), Decimal(90), Decimal(100), Decimal(100), 1))
        bars.extend([
            Bar(start + timedelta(minutes=6 + i), start + timedelta(minutes=7 + i),
                Decimal(100 + i), Decimal(101) + Decimal(i), Decimal(99) + Decimal(i),
                Decimal(100 + i), Decimal(100), 1)
            for i in range(6)
        ])
        self.assertEqual(causal_regime(bars, bars[-1].bar_end), Regime.TREND_DAY)


class CostRuleTests(unittest.TestCase):
    def setUp(self):
        self.costs = load_costs("config/costs.yaml")

    def _values(self, allow_estimates=False):
        from app.backtest.dataset_runner import _cost_model
        values, _excluded = _cost_model(self.costs, "US_EQUITIES", allow_estimates)
        return values

    def test_commission_minimum_per_order(self):
        v = self._values()
        self.assertEqual(_commission(v, Decimal(100), Decimal(10000)), Decimal("0.35"))

    def test_sell_side_regulatory_fees_only_on_exit(self):
        v = self._values()
        qty, price = Decimal(100), Decimal(100)
        notional = qty * price
        sell_only = v["sec_fee_sell_rate"] * notional + v["finra_taf_sell_per_share"] * qty
        shared = (_commission(v, qty, notional) + v["finra_cat_per_share"] * qty
                  + v["clearing_nscc_dtc_per_share"] * qty)
        self.assertEqual(exit_costs(v, qty, price), shared + sell_only)
        self.assertEqual(entry_costs(v, qty, price), shared + v["spread_pct_full"] * notional)

    def test_unverified_slippage_excluded_by_default(self):
        from app.backtest.dataset_runner import _cost_model
        _values, excluded = _cost_model(self.costs, "US_EQUITIES", False)
        self.assertIn("slippage_pct_per_side", excluded)
        self.assertIn("exchange_fee_per_share", excluded)

    def test_explicit_estimate_flag_enables_estimated_slippage(self):
        from app.backtest.dataset_runner import _cost_model
        values, excluded = _cost_model(self.costs, "US_EQUITIES", True)
        self.assertNotIn("slippage_pct_per_side", excluded)
        self.assertTrue(values["_slippage_on"])

    def test_crypto_cost_fields_match_crypto_spot_schema(self):
        for allow_estimates in (False, True):
            _values, excluded = _cost_model(self.costs, "CRYPTO_MAJORS", allow_estimates)
            self.assertEqual(excluded, ["commission", "spread_pct_full"])

    def test_unmapped_cost_profile_uses_generic_cost_fields(self):
        values, excluded = _cost_model(self.costs, "UK_LSE", False)
        self.assertIn("commission", values)
        self.assertIn("spread_pct_full", values)
        self.assertEqual(excluded, ["commission", "spread_pct_full"])

    def test_summary_labels_gross_partial_and_fully_net_coverage(self):
        from app.backtest.dataset_runner import summarize
        base = {"symbol": "SPY", "side": "long", "qty": Decimal(1),
                "entry_time": datetime(2026, 11, 2, 14, 30, tzinfo=UTC),
                "entry_price": Decimal(100), "exit_time": datetime(2026, 11, 2, 14, 35, tzinfo=UTC),
                "exit_price": Decimal(101), "exit_reason": "TARGET_HIT", "risk_amount": Decimal(1),
                "gross_pnl": Decimal(1), "cost": None, "r_multiple": Decimal(1),
                "holding_minutes": Decimal(5)}
        gross = Trade(**base, net_pnl=None)
        net = Trade(**{**base, "cost": Decimal("0.1")}, net_pnl=Decimal("0.9"))
        self.assertEqual(summarize([gross], Decimal(100))["cost_status"], "gross")
        self.assertEqual(summarize([net], Decimal(100))["cost_status"], "fully_net")
        self.assertEqual(summarize([gross, net], Decimal(100))["cost_status"], "partial_net")

    def test_unknown_profile_runs_with_default_session(self):
        engine = SimpleNamespace(evaluate=lambda **_kwargs: SimpleNamespace(action="hold"))
        result = run_symbol("UNKNOWN", [_bar(0, "100", "101", "99", "100")], "UNKNOWN",
                            engine, None, self.costs, Decimal(25000), Decimal("0.0025"), False)
        self.assertFalse(result["net_available"])
        self.assertEqual(result["excluded_cost_fields"], ["commission", "spread_pct_full"])

    def test_commodity_costs_inherit_etf_fees_and_are_available(self):
        from app.backtest.dataset_runner import _cost_model
        values, excluded = _cost_model(self.costs, "US_COMMODITIES", False)
        self.assertEqual(values["commission_per_share"], Decimal("0.0035"))
        self.assertEqual(values["sec_fee_sell_rate"], Decimal("0.0000206"))
        self.assertNotIn("commission_per_share", excluded)
        result = run_symbol("GLD", [], "US_COMMODITIES", None, None, self.costs,
                            Decimal(25000), Decimal("0.0025"), False)
        self.assertFalse(result["net_available"])
        self.assertTrue(result["excluded_cost_fields"])

    def test_whole_share_profiles_floor_but_crypto_remains_fractional(self):
        class EntryOnceEngine:
            stop_mult = Decimal("0.75")

            def __init__(self):
                self.entered = False

            def evaluate(self, **kwargs):
                if not self.entered and kwargs["snapshot"].atr is not None:
                    self.entered = True
                    return SimpleNamespace(action="enter_long", parameters={"target": "110"})
                return SimpleNamespace(action="hold", parameters={})

        policy = TradingPolicy.from_yaml("config/markets_17.yaml")
        for symbol, profile, start, expected_qty in (
            ("GLD", "US_COMMODITIES", datetime(2026, 11, 2, 14, 30, tzinfo=UTC), Decimal(13)),
            ("NIFTYBEES.NS", "INDIA_NSE", datetime(2026, 11, 2, 3, 45, tzinfo=UTC), Decimal(13)),
            ("BTC-USD", "CRYPTO_MAJORS", datetime(2026, 11, 2, tzinfo=UTC), Decimal(20) / Decimal("1.5")),
        ):
            bars = [
                Bar(start + timedelta(minutes=i), start + timedelta(minutes=i + 1),
                    Decimal(100), Decimal(101), Decimal(99), Decimal(100), Decimal(100), 1)
                for i in range(22)
            ]
            result = run_symbol(symbol, bars, profile, EntryOnceEngine(), policy, self.costs,
                                Decimal(25000), Decimal("0.0025"), False)
            self.assertEqual(result["trades"][0].risk_amount, Decimal(20))
            self.assertEqual(result["trades"][0].qty, expected_qty)

    def test_reaction_zone_size_weight_reduces_risk_budget(self):
        class EntryOnceEngine:
            stop_mult = Decimal("0.75")

            def __init__(self):
                self.entered = False

            def evaluate(self, **kwargs):
                if not self.entered and kwargs["snapshot"].atr is not None:
                    self.entered = True
                    return SimpleNamespace(action="enter_long", parameters={
                        "target": "110", "size_weight": "0.68",
                    })
                return SimpleNamespace(action="hold", parameters={})

        start = datetime(2026, 11, 2, 14, 30, tzinfo=UTC)
        bars = [
            Bar(start + timedelta(minutes=i), start + timedelta(minutes=i + 1),
                Decimal(100), Decimal("100.1"), Decimal("99.9"), Decimal(100), Decimal(100), 1)
            for i in range(22)
        ]
        policy = TradingPolicy.from_yaml("config/markets_17.yaml")
        result = run_symbol("SPY", bars, "US_EQUITIES", EntryOnceEngine(), policy, self.costs,
                            Decimal(25000), Decimal("0.0025"), False)
        # The policy's 0.08% cap gives $20; the shallow-zone weight scales it to $13.60.
        self.assertEqual(result["trades"][0].risk_amount, Decimal("13.6000"))

    def test_next_bar_gap_rejects_entry_when_structural_reward_risk_collapses(self):
        class EntryOnceEngine:
            stop_mult = Decimal("0.75")
            min_rr = Decimal("1.5")

            def __init__(self):
                self.entered = False

            def evaluate(self, **kwargs):
                if not self.entered and kwargs["snapshot"].atr is not None:
                    self.entered = True
                    return SimpleNamespace(action="enter_long", parameters={
                        "target": "101.9", "target_source": "SWING_HIGH",
                    })
                return SimpleNamespace(action="hold", parameters={})

        start = datetime(2026, 11, 2, 14, 30, tzinfo=UTC)
        bars = []
        for i in range(22):
            o, h, l, c = Decimal(100), Decimal("100.1"), Decimal("99.9"), Decimal(100)
            if i == 2:
                o, h, l, c = Decimal("101.8"), Decimal("101.9"), Decimal("101.7"), Decimal("101.8")
            bars.append(Bar(start + timedelta(minutes=i), start + timedelta(minutes=i + 1),
                            o, h, l, c, Decimal(100), 1))
        policy = TradingPolicy.from_yaml("config/markets_17.yaml")
        result = run_symbol("SPY", bars, "US_EQUITIES", EntryOnceEngine(), policy, self.costs,
                            Decimal(25000), Decimal("0.0025"), False)
        self.assertEqual(result["trades"], [])

    def test_atr_open_space_target_distance_is_reanchored_to_fill(self):
        class EntryOnceEngine:
            stop_mult = Decimal("0.75")
            min_rr = Decimal("1.5")

            def __init__(self):
                self.entered = False

            def evaluate(self, **kwargs):
                if not self.entered and kwargs["snapshot"].atr is not None:
                    self.entered = True
                    return SimpleNamespace(action="enter_long", parameters={
                        "target": "102", "target_source": "ATR_OPEN_SPACE",
                    })
                return SimpleNamespace(action="hold", parameters={})

        start = datetime(2026, 11, 2, 14, 30, tzinfo=UTC)
        bars = []
        for i in range(22):
            o, h, l, c = Decimal(100), Decimal("100.1"), Decimal("99.9"), Decimal(100)
            if i == 2:
                o, h, l, c = Decimal(101), Decimal("103.1"), Decimal("100.9"), Decimal(101)
            bars.append(Bar(start + timedelta(minutes=i), start + timedelta(minutes=i + 1),
                            o, h, l, c, Decimal(100), 1))
        policy = TradingPolicy.from_yaml("config/markets_17.yaml")
        result = run_symbol("SPY", bars, "US_EQUITIES", EntryOnceEngine(), policy, self.costs,
                            Decimal(25000), Decimal("0.0025"), False)
        self.assertEqual(result["trades"][0].exit_reason, "TARGET_HIT")
        self.assertEqual(result["trades"][0].exit_price, Decimal(103))

    def test_run_dataset_rejects_nonconfigured_signal_interval(self):
        with self.assertRaisesRegex(ValueError, "does not match configured"):
            run_dataset(FIXTURE, interval_seconds=60)

    def test_run_dataset_does_not_duplicate_configured_core_symbols(self):
        with patch("app.backtest.dataset_runner.TradingPolicy", wraps=TradingPolicy) as policy_factory:
            run_dataset(
                FIXTURE,
                profile_map={"SPY": "US_EQUITIES"},
                instrument_symbols={999: "SPY"},
            )
        markets = policy_factory.call_args.args[0]["markets"]
        self.assertEqual(markets["US_EQUITIES"]["core_symbols"].count("SPY"), 1)

    def test_calibrated_market_thresholds(self):
        import yaml

        config = yaml.safe_load(Path("config/markets_17.yaml").read_text(encoding="utf-8"))["markets"]
        expected = {
            "US_EQUITIES": (0.0032, 0.95),
            "US_COMMODITIES": (0.0035, 0.95),
            "CRYPTO_MAJORS": (0.0110, 1.15),
        }
        for market, (move_room, rvol) in expected.items():
            self.assertEqual(config[market]["min_net_moveroom_pct"], move_room)
            self.assertEqual(config[market]["slot_rvol_min"], rvol)

    def test_strategy_moveroom_cost_and_slippage_total(self):
        import yaml

        strategy = yaml.safe_load(
            Path("config/strategies/confluence_pullback.yaml").read_text(encoding="utf-8")
        )["strategy"]["move_room"]
        self.assertEqual(
            strategy["roundtrip_cost_pct"] + strategy["slippage_buffer_pct"],
            0.0015,
        )


class IntrabarExitTests(unittest.TestCase):
    def setUp(self):
        self.costs = load_costs("config/costs.yaml")

    def _pos(self, side):
        return Position(side, Decimal(10), datetime(2026, 11, 2, tzinfo=UTC), Decimal(100),
                        Decimal(99) if side > 0 else Decimal(101),
                        Decimal(102) if side > 0 else Decimal(98), Decimal(10), None)

    def test_long_stop_hit(self):
        self.assertEqual(_intrabar_exit(self._pos(1), _bar(1, "100", "100", "98.5", "99"))[1], "HARD_STOP")

    def test_long_target_hit(self):
        self.assertEqual(_intrabar_exit(self._pos(1), _bar(1, "100", "102.5", "100", "102"))[1], "TARGET_HIT")

    def test_stop_wins_when_both_hit_in_one_bar(self):
        price, reason = _intrabar_exit(self._pos(1), _bar(1, "100", "103", "98", "100"))
        self.assertEqual((price, reason), (Decimal(99), "HARD_STOP_BOTH_TOUCHED"))

    def test_short_stop_hit(self):
        self.assertEqual(_intrabar_exit(self._pos(-1), _bar(1, "100", "101.5", "100", "101"))[1], "HARD_STOP")

    def test_short_both_levels_touched_is_explicitly_ambiguous(self):
        price, reason = _intrabar_exit(self._pos(-1), _bar(1, "100", "103", "97", "100"))
        self.assertEqual((price, reason), (Decimal(101), "HARD_STOP_BOTH_TOUCHED"))

    def test_long_gap_through_stop_fills_at_bar_open(self):
        price, reason = _intrabar_exit(self._pos(1), _bar(1, "98", "100", "97", "99"))
        self.assertEqual((price, reason), (Decimal(98), "HARD_STOP_GAP"))

    def test_short_gap_through_stop_fills_at_bar_open(self):
        price, reason = _intrabar_exit(self._pos(-1), _bar(1, "102", "103", "100", "101"))
        self.assertEqual((price, reason), (Decimal(102), "HARD_STOP_GAP"))

    def test_strategy_config_sets_four_hour_and_stagnation_limits(self):
        import yaml

        exit_config = yaml.safe_load(
            Path("config/strategies/confluence_pullback.yaml").read_text(encoding="utf-8")
        )["strategy"]["exit"]
        self.assertEqual(exit_config["max_holding_minutes"], 240)
        self.assertEqual(exit_config["stagnation_minutes"], 60)
        self.assertEqual(exit_config["stagnation_min_favorable_r"], 0.25)

    def test_max_holding_time_closes_from_actual_fill_timestamp(self):
        class EntryOnceEngine:
            stop_mult = Decimal("0.75")
            def __init__(self):
                self.strategy = {"exit": {"max_holding_minutes": 3, "stagnation_minutes": 0}}
                self.entered = False

            def evaluate(self, **kwargs):
                if not self.entered and kwargs["snapshot"].atr is not None:
                    self.entered = True
                    return SimpleNamespace(action="enter_long", parameters={"target": "110"})
                return SimpleNamespace(action="hold", parameters={})

        start = datetime(2026, 11, 2, 14, 30, tzinfo=UTC)
        bars = [
            Bar(start + timedelta(minutes=i), start + timedelta(minutes=i + 1),
                Decimal(100), Decimal("100.1"), Decimal("99.9"), Decimal(100), Decimal(100), 1)
            for i in range(22)
        ]
        policy = TradingPolicy.from_yaml("config/markets_17.yaml")
        result = run_symbol("SPY", bars, "US_EQUITIES", EntryOnceEngine(), policy, self.costs,
                            Decimal(25000), Decimal("0.0025"), False)
        trade = result["trades"][0]
        self.assertEqual(trade.exit_reason, "MAX_HOLD_TIME")
        self.assertEqual((trade.exit_time - trade.entry_time).total_seconds(), 3 * 60)
        self.assertEqual(trade.holding_minutes, Decimal(3))

    def test_runner_uses_order_simulator_partial_fill_and_scales_risk(self):
        class EntryOnceEngine:
            stop_mult = Decimal("0.75")
            def __init__(self):
                self.entered = False
                self.strategy = {"exit": {"max_holding_minutes": 3, "stagnation_minutes": 0}}
            def evaluate(self, **kwargs):
                if not self.entered and kwargs["snapshot"].atr is not None:
                    self.entered = True
                    return SimpleNamespace(action="enter_long", parameters={"target": "110"})
                return SimpleNamespace(action="hold", parameters={})
        start = datetime(2026, 11, 2, 14, 30, tzinfo=UTC)
        bars = [Bar(start + timedelta(minutes=i), start + timedelta(minutes=i + 1),
                    Decimal(100), Decimal("100.1"), Decimal("99.9"), Decimal(100), Decimal(100), 1)
                for i in range(22)]
        policy = TradingPolicy.from_yaml("config/markets_17.yaml")
        simulator = OrderSimulator(SimulationConfig(partial_fill_ratio=Decimal("0.5")))
        result = run_symbol("SPY", bars, "US_EQUITIES", EntryOnceEngine(), policy, self.costs,
                            Decimal(25000), Decimal("0.0025"), False, simulator)
        trade = result["trades"][0]
        self.assertEqual(trade.fill_status, "partial")
        self.assertEqual(result["execution"]["partial_fills"], 1)
        self.assertGreater(trade.risk_amount, Decimal(0))
        self.assertLess(trade.risk_amount, Decimal("62.5"))

    def test_runner_records_rejected_simulated_entry(self):
        class RejectingSimulator:
            def submit(self, bar, side, order_type, quantity):
                return Fill(bar.bar_start, side, Decimal(0), Decimal(0), Decimal(0), "rejected")
        class EntryOnceEngine:
            stop_mult = Decimal("0.75")
            def __init__(self):
                self.entered = False
            def evaluate(self, **kwargs):
                if not self.entered and kwargs["snapshot"].atr is not None:
                    self.entered = True
                    return SimpleNamespace(action="enter_long", parameters={"target": "110"})
                return SimpleNamespace(action="hold", parameters={})
        start = datetime(2026, 11, 2, 14, 30, tzinfo=UTC)
        bars = [Bar(start + timedelta(minutes=i), start + timedelta(minutes=i + 1),
                    Decimal(100), Decimal("100.1"), Decimal("99.9"), Decimal(100), Decimal(100), 1)
                for i in range(22)]
        policy = TradingPolicy.from_yaml("config/markets_17.yaml")
        result = run_symbol("SPY", bars, "US_EQUITIES", EntryOnceEngine(), policy, self.costs,
                            Decimal(25000), Decimal("0.0025"), False, RejectingSimulator())
        self.assertEqual(result["execution"]["rejected_entries"], 1)
        self.assertEqual(result["trades"], [])

    def test_entry_bar_stop_is_checked_immediately_after_fill(self):
        class EntryOnceEngine:
            stop_mult = Decimal("0.75")

            def __init__(self):
                self.entered = False

            def evaluate(self, **kwargs):
                if not self.entered and kwargs["snapshot"].atr is not None:
                    self.entered = True
                    return SimpleNamespace(action="enter_long", parameters={"target": "110"})
                return SimpleNamespace(action="hold", parameters={})

        start = datetime(2026, 11, 2, 14, 30, tzinfo=UTC)
        bars = [
            Bar(start + timedelta(minutes=i), start + timedelta(minutes=i + 1),
                Decimal(100), Decimal("100.1"), Decimal("99.9"), Decimal(100), Decimal(100), 1)
            for i in range(22)
        ]
        bars[2] = Bar(bars[2].bar_start, bars[2].bar_end, Decimal(100), Decimal("100.1"),
                      Decimal("99.7"), Decimal("99.8"), Decimal(100), 1)
        policy = TradingPolicy.from_yaml("config/markets_17.yaml")
        result = run_symbol("SPY", bars, "US_EQUITIES", EntryOnceEngine(), policy, self.costs,
                            Decimal(25000), Decimal("0.0025"), False)
        self.assertEqual(result["trades"][0].exit_reason, "HARD_STOP")
        self.assertEqual(result["trades"][0].entry_time, bars[2].bar_start)

    def test_max_holding_gap_uses_last_pre_deadline_close(self):
        class EntryOnceEngine:
            stop_mult = Decimal("0.75")
            def __init__(self):
                self.strategy = {"exit": {"max_holding_minutes": 3, "stagnation_minutes": 0}}
                self.entered = False

            def evaluate(self, **kwargs):
                if not self.entered and kwargs["snapshot"].atr is not None:
                    self.entered = True
                    return SimpleNamespace(action="enter_long", parameters={"target": "110"})
                return SimpleNamespace(action="hold", parameters={})

        start = datetime(2026, 11, 2, 14, 30, tzinfo=UTC)
        offsets = [0, 1, 2, 3, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21]
        bars = [
            Bar(start + timedelta(minutes=i), start + timedelta(minutes=i + 1),
                Decimal(100), Decimal("100.1"), Decimal("99.9"), Decimal(100), Decimal(100), 1)
            for i in offsets
        ]
        policy = TradingPolicy.from_yaml("config/markets_17.yaml")
        result = run_symbol("SPY", bars, "US_EQUITIES", EntryOnceEngine(), policy, self.costs,
                            Decimal(25000), Decimal("0.0025"), False)
        trade = result["trades"][0]
        self.assertEqual(trade.exit_reason, "MAX_HOLD_TIME_DATA_GAP")
        self.assertLessEqual(trade.holding_minutes, Decimal(3))

    def test_stagnation_exit_requires_insufficient_favourable_excursion(self):
        class EntryOnceEngine:
            stop_mult = Decimal("0.75")
            def __init__(self):
                self.strategy = {"exit": {"max_holding_minutes": 240, "stagnation_minutes": 2,
                                          "stagnation_min_favorable_r": "1.0"}}
                self.entered = False

            def evaluate(self, **kwargs):
                if not self.entered and kwargs["snapshot"].atr is not None:
                    self.entered = True
                    return SimpleNamespace(action="enter_long", parameters={"target": "110"})
                return SimpleNamespace(action="hold", parameters={})

        start = datetime(2026, 11, 2, 14, 30, tzinfo=UTC)
        bars = [
            Bar(start + timedelta(minutes=i), start + timedelta(minutes=i + 1),
                Decimal(100), Decimal("100.1"), Decimal("99.9"), Decimal(100), Decimal(100), 1)
            for i in range(22)
        ]
        policy = TradingPolicy.from_yaml("config/markets_17.yaml")
        result = run_symbol("SPY", bars, "US_EQUITIES", EntryOnceEngine(), policy, self.costs,
                            Decimal(25000), Decimal("0.0025"), False)
        self.assertEqual(result["trades"][0].exit_reason, "STAGNATION_TIME")


class SummaryTests(unittest.TestCase):
    def test_empty_summary(self):
        self.assertEqual(summarize([], Decimal(1000)), {"trades": 0, "cost_status": "no_trades"})

    def test_summary_breaks_down_long_short_by_entry_regime(self):
        def trade(side, regime, pnl):
            return Trade("SPY", side, Decimal(1), datetime(2026, 11, 2, 14, 30, tzinfo=UTC),
                         Decimal(100), datetime(2026, 11, 2, 14, 35, tzinfo=UTC), Decimal(101),
                         "TARGET_HIT", Decimal(1), Decimal(pnl), None, None, None,
                         Decimal(5), regime)
        out = summarize([trade("long", "TREND_DAY", "1"),
                         trade("short", "TREND_DAY", "-1"),
                         trade("long", "CHOP_DAY", "-2")], Decimal(1000))
        self.assertEqual(out["by_regime"]["TREND_DAY"]["trades"], 2)
        self.assertEqual(out["by_regime_side"]["TREND_DAY"]["long"]["total_pnl"], 1.0)
        self.assertEqual(out["by_regime_side"]["TREND_DAY"]["short"]["total_pnl"], -1.0)
        self.assertEqual(out["by_regime_side"]["CHOP_DAY"]["long"]["total_pnl"], -2.0)

    def test_profit_factor_and_drawdown(self):
        t = lambda pnl: Trade("X", "long", Decimal(1), datetime(2026, 1, 1, tzinfo=UTC), Decimal(1),
                              datetime(2026, 1, 1, tzinfo=UTC), Decimal(1), "T", Decimal(1), pnl, None, None, None)
        out = summarize([t(Decimal(30)), t(Decimal(-10)), t(Decimal(-10))], Decimal(100))["gross"]
        self.assertEqual(out["trades"], 3)
        self.assertAlmostEqual(out["profit_factor"], 1.5)
        self.assertAlmostEqual(out["max_drawdown_pct"], 20 / 130 * 100, places=3)


class EndToEndSyntheticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out_dir = tempfile.mkdtemp()
        cls.result = run_dataset(FIXTURE, interval_seconds=300)
        cls.trades = cls.result.pop("_trades")

    def test_both_symbols_run_and_crypto_net_is_blocked(self):
        self.assertEqual(set(self.result["per_symbol"]), {"SIM-EQ", "SIM-COIN"})
        self.assertFalse(self.result["per_symbol"]["SIM-COIN"]["net_costs_available"])

    def test_equity_positions_never_held_overnight_or_past_close(self):
        for t in self.trades:
            if t.symbol != "SIM-EQ":
                continue
            self.assertEqual(t.entry_time.date(), t.exit_time.date())
            self.assertLessEqual(t.exit_time.hour * 60 + t.exit_time.minute, 21 * 60)

    def test_equity_risk_respects_causal_regime_cap(self):
        for t in self.trades:
            if t.symbol == "SIM-EQ":
                base_risks = {
                    Decimal(25000) * Decimal("0.0008"),
                    Decimal(25000) * Decimal("0.0025"),
                }
                allowed_risks = {
                    base * weight for base in base_risks
                    for weight in (Decimal(1), Decimal("0.68"), Decimal("0.75"))
                }
                self.assertIn(t.risk_amount, allowed_risks)

    def test_equity_quantities_are_whole_shares(self):
        for t in self.trades:
            if t.symbol == "SIM-EQ":
                self.assertEqual(t.qty, t.qty.to_integral_value())

    def test_deterministic_rerun(self):
        again = run_dataset(FIXTURE, interval_seconds=300)
        again.pop("_trades")
        self.result.pop("net_definition", None)
        again.pop("net_definition", None)
        self.assertEqual(self.result, again)


if __name__ == "__main__":
    unittest.main()
