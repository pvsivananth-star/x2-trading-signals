"""Shared portfolio ledger constraints and deterministic reservation behavior."""

import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.backtest.portfolio import PortfolioLedger


class PortfolioLedgerTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 2, 14, 30, tzinfo=UTC)
        self.ledger = PortfolioLedger(
            Decimal(100000),
            {"US_EQUITIES": Decimal("0.35"), "US_COMMODITIES": Decimal("0.20")},
            {"US_TECH_INDEX": Decimal("0.005"), "COMMODITIES": Decimal("0.0035")},
            max_concurrent_positions=2,
            max_total_open_risk_pct=Decimal("0.01"),
        )

    def _reserve(self, symbol="SPY", market="US_EQUITIES", cluster="US_TECH_INDEX",
                 qty=Decimal(100), price=Decimal(100), risk=Decimal(100),
                 exit_after=60, stop=Decimal(1)):
        return self.ledger.reserve(
            timestamp=self.now,
            exit_time=self.now + timedelta(minutes=exit_after),
            symbol=symbol,
            market=market,
            cluster=cluster,
            requested_quantity=qty,
            entry_price=price,
            stop_distance=stop,
            max_risk_amount=risk,
        )

    def test_reserves_cash_and_market_allocation_from_shared_capital(self):
        first = self._reserve(qty=Decimal(400), price=Decimal(100), risk=Decimal(400))
        self.assertTrue(first.accepted)
        self.assertEqual(first.notional, Decimal(35000))
        second = self._reserve(symbol="QQQ", qty=Decimal(100), price=Decimal(100), risk=Decimal(100))
        self.assertFalse(second.accepted)
        self.assertEqual(second.reason, "market_allocation")

    def test_cluster_risk_cap_scales_later_candidate(self):
        first = self._reserve(qty=Decimal(400), price=Decimal(50), risk=Decimal(400))
        self.assertTrue(first.accepted)
        second = self._reserve(symbol="QQQ", qty=Decimal(400), price=Decimal(50), risk=Decimal(400))
        self.assertTrue(second.accepted)
        self.assertEqual(second.status, "scaled")
        self.assertEqual(second.risk_amount, Decimal(100))

    def test_max_concurrent_positions_blocks_third_live_reservation(self):
        self.assertTrue(self._reserve(symbol="A").accepted)
        self.assertTrue(self._reserve(symbol="B", market="US_COMMODITIES", cluster="COMMODITIES").accepted)
        third = self._reserve(symbol="C")
        self.assertFalse(third.accepted)
        self.assertEqual(third.reason, "max_concurrent_positions")

    def test_expired_reservations_release_cash_and_concurrency(self):
        self.assertTrue(self._reserve(qty=Decimal(300), price=Decimal(100), risk=Decimal(300)).accepted)
        later = self.now + timedelta(minutes=60)
        decision = self.ledger.reserve(
            timestamp=later,
            exit_time=later + timedelta(minutes=30),
            symbol="QQQ",
            market="US_EQUITIES",
            cluster="US_TECH_INDEX",
            requested_quantity=Decimal(100),
            entry_price=Decimal(100),
            stop_distance=Decimal(1),
            max_risk_amount=Decimal(100),
        )
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.status, "accepted")

    def test_disabled_market_and_missing_cluster_fail_closed(self):
        disabled = self._reserve(market="NOT_ENABLED", cluster="US_TECH_INDEX")
        self.assertEqual(disabled.reason, "market_allocation_disabled")
        missing = self._reserve(cluster="UNKNOWN")
        self.assertEqual(missing.reason, "cluster_risk_cap_missing")


if __name__ == "__main__":
    unittest.main()
