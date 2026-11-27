import unittest
from decimal import Decimal

from app.costs import CostBlockedError, load_costs, required_gross_move, us_equity_roundtrip


class CostConfigTests(unittest.TestCase):
    def setUp(self):
        self.costs = load_costs("config/costs.yaml")

    def test_exchange_fee_not_found_blocks_us_equities(self):
        with self.assertRaises(CostBlockedError):
            us_equity_roundtrip(self.costs, Decimal(524), Decimal(100))

    def test_crypto_spot_blocked(self):
        from app.costs import _field
        with self.assertRaises(CostBlockedError):
            _field(self.costs, "CRYPTO_BTC_SPOT", "commission")

    def test_etf_inherits_equity_costs(self):
        from app.costs import _field
        self.assertEqual(_field(self.costs, "US_ETF", "commission_per_share"), Decimal("0.0035"))
        self.assertEqual(_field(self.costs, "US_ETF", "sec_fee_sell_rate"), Decimal("0.0000206"))

    def test_inheritance_cycle_is_blocked(self):
        from app.costs import _field
        costs = {"A": {"inherits": "B"}, "B": {"inherits": "A"}}
        with self.assertRaisesRegex(CostBlockedError, "inheritance cycle"):
            _field(costs, "A", "commission_per_share")

    def test_estimate_is_blocked_unless_allowed(self):
        self.costs["allow_estimates"] = False
        with self.assertRaises(CostBlockedError):
            us_equity_roundtrip(
                {**self.costs, "US_EQUITIES": {**self.costs["US_EQUITIES"],
                                                "exchange_fee_per_share": {"value": 0.003, "status": "verified"}}},
                Decimal(524), Decimal(100))

    def test_commission_minimum_applies(self):
        patched = {**self.costs, "US_EQUITIES": {**self.costs["US_EQUITIES"],
                   "exchange_fee_per_share": {"value": 0, "status": "verified"},
                   "slippage_pct_per_side": {"value": 0, "status": "verified"}}}
        rt = us_equity_roundtrip(patched, Decimal(524), Decimal(100))
        # 100 sh * 0.0035 = 0.35 = minimum per side, so 0.70 round trip commission
        self.assertEqual(rt.commission, Decimal("0.70"))

    def test_required_gross_move(self):
        self.assertEqual(required_gross_move(Decimal("0.0045"), Decimal("0.0003")), Decimal("0.0048"))


if __name__ == "__main__":
    unittest.main()
