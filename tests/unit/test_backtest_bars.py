import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.backtest.bars import Trade, rollup


class BacktestBarTests(unittest.TestCase):
    def test_rollup_boundaries_are_deterministic(self) -> None:
        start = datetime(2026, 11, 20, 0, 0, 1, tzinfo=UTC)
        trades = [
            Trade(start, Decimal(100), Decimal(1)),
            Trade(start + timedelta(seconds=58), Decimal(104), Decimal(2)),
            Trade(start + timedelta(seconds=60), Decimal(102), Decimal(3)),
        ]
        bars = rollup(trades, 60)

        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0].open, Decimal(100))
        self.assertEqual(bars[0].high, Decimal(104))
        self.assertEqual(bars[0].close, Decimal(104))
        self.assertEqual(bars[0].volume, Decimal(3))
        self.assertEqual(bars[1].open, Decimal(102))
        self.assertEqual(bars[1].trade_count, 1)

    def test_empty_rollup_has_no_bars(self) -> None:
        self.assertEqual(rollup([], 300), [])


if __name__ == "__main__":
    unittest.main()
