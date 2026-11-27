"""Dataset backtest runner: session timing, cost rules, intrabar exits, and end-to-end invariants."""

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from app.backtest.bars import Bar
from app.backtest.dataset_runner import (
    Position,
    Trade,
    _commission,
    _intrabar_exit,
    entry_costs,
    exit_costs,
    minutes_to_close,
    run_dataset,
    summarize,
)
from app.costs import load_costs

FIXTURE = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "synthetic_ci"


def _bar(minute: int, o: str, h: str, l: str, c: str) -> Bar:
    start = datetime(2026, 11, 2, 14, 30, tzinfo=UTC) + timedelta(minutes=minute)
    return Bar(start, start + timedelta(minutes=1), Decimal(o), Decimal(h), Decimal(l), Decimal(c), Decimal(100), 1)


class SessionTimingTests(unittest.TestCase):
    def test_equity_minutes_to_close(self):
        self.assertEqual(minutes_to_close(datetime(2026, 11, 2, 20, 45, tzinfo=UTC), "US_EQUITIES"), 15)
        self.assertEqual(minutes_to_close(datetime(2026, 11, 2, 21, 0, tzinfo=UTC), "US_EQUITIES"), 0)

    def test_crypto_has_no_session_clock(self):
        self.assertIsNone(minutes_to_close(datetime(2026, 11, 2, 20, 45, tzinfo=UTC), "CRYPTO_MAJORS"))


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
        from app.backtest.dataset_runner import _cost_model
        for allow_estimates in (False, True):
            _values, excluded = _cost_model(self.costs, "CRYPTO_MAJORS", allow_estimates)
            self.assertEqual(excluded, ["commission", "spread_pct_full"])


class IntrabarExitTests(unittest.TestCase):
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
        self.assertEqual((price, reason), (Decimal(99), "HARD_STOP"))

    def test_short_stop_hit(self):
        self.assertEqual(_intrabar_exit(self._pos(-1), _bar(1, "100", "101.5", "100", "101"))[1], "HARD_STOP")


class SummaryTests(unittest.TestCase):
    def test_empty_summary(self):
        self.assertEqual(summarize([], Decimal(1000)), {"trades": 0})

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

    def test_equity_risk_is_quarter_percent_of_allocation(self):
        for t in self.trades:
            if t.symbol == "SIM-EQ":
                self.assertEqual(t.risk_amount, Decimal(25000) * Decimal("0.0025"))

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
