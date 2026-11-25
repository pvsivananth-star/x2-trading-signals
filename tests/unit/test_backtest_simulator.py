import unittest
from datetime import datetime, timezone
from decimal import Decimal

from app.backtest.bars import Bar
from app.backtest.simulator import replay_market_buys


UTC = timezone.utc


class BacktestSimulatorTests(unittest.TestCase):
    def test_market_buy_replay_is_deterministic(self) -> None:
        bar1 = Bar(datetime(2026, 11, 20, tzinfo=UTC), datetime(2026, 11, 20, 0, 1, tzinfo=UTC), Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100"), Decimal("10"), 1)
        bar2 = Bar(datetime(2026, 11, 20, 0, 1, tzinfo=UTC), datetime(2026, 11, 20, 0, 2, tzinfo=UTC), Decimal("100"), Decimal("111"), Decimal("99"), Decimal("110"), Decimal("10"), 1)

        first = replay_market_buys([bar2, bar1], starting_cash=Decimal("300"), quantity_per_fill=Decimal("1"), fee_rate=Decimal("0.001"), slippage_bps=Decimal("10"))
        second = replay_market_buys([bar1, bar2], starting_cash=Decimal("300"), quantity_per_fill=Decimal("1"), fee_rate=Decimal("0.001"), slippage_bps=Decimal("10"))

        self.assertEqual(first, second)
        self.assertEqual(first.ending_quantity, Decimal("2"))
        self.assertEqual(len(first.fills), 2)


if __name__ == "__main__":
    unittest.main()
