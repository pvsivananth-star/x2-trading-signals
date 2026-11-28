import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.backtest.bars import Bar, Trade, aggregate_bars, rollup
from app.features import causal_confirmed_swing


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

    def test_aggregate_emits_only_complete_contiguous_higher_timeframe_bars(self) -> None:
        start = datetime(2026, 11, 20, 14, 30, tzinfo=UTC)
        bars = [
            Bar(start + timedelta(minutes=5 * i), start + timedelta(minutes=5 * (i + 1)),
                Decimal(100 + i), Decimal(101 + i), Decimal(99 + i), Decimal(100 + i),
                Decimal(10), 1)
            for i in range(3)
        ]
        aggregated = aggregate_bars(bars, 15 * 60, 5 * 60)
        self.assertEqual(len(aggregated), 1)
        self.assertEqual((aggregated[0].open, aggregated[0].high, aggregated[0].low,
                          aggregated[0].close, aggregated[0].volume),
                         (Decimal(100), Decimal(103), Decimal(99), Decimal(102), Decimal(30)))

    def test_aggregate_rejects_missing_or_unclosed_source_bars(self) -> None:
        start = datetime(2026, 11, 20, 14, 30, tzinfo=UTC)
        bars = [
            Bar(start + timedelta(minutes=5 * i), start + timedelta(minutes=5 * (i + 1)),
                Decimal(100), Decimal(101), Decimal(99), Decimal(100), Decimal(10), 1)
            for i in (0, 2)
        ]
        self.assertEqual(aggregate_bars(bars, 15 * 60, 5 * 60), [])
        complete = [
            Bar(start + timedelta(minutes=5 * i), start + timedelta(minutes=5 * (i + 1)),
                Decimal(100), Decimal(101), Decimal(99), Decimal(100), Decimal(10), 1,
                final=(i < 2), partial=(i == 2))
            for i in range(3)
        ]
        self.assertEqual(aggregate_bars(complete, 15 * 60, 5 * 60), [])

    def test_confirmed_swing_requires_two_closed_confirmation_bars(self) -> None:
        start = datetime(2026, 11, 20, 14, 30, tzinfo=UTC)
        highs_lows = [(101, 99), (102, 98), (105, 100), (103, 99),
                      (102, 98), (101, 95), (102, 97), (103, 98)]
        bars = [
            Bar(start + timedelta(minutes=15 * i), start + timedelta(minutes=15 * (i + 1)),
                Decimal(100), Decimal(high), Decimal(low), Decimal(100), Decimal(10), 1)
            for i, (high, low) in enumerate(highs_lows)
        ]
        early = bars[:4]
        self.assertFalse(causal_confirmed_swing(early, early[-1].bar_end, Decimal(2))[-1])
        high, low, direction, size_atr, confirmed = causal_confirmed_swing(
            bars, bars[-1].bar_end, Decimal(2), left_bars=3, confirmation_bars=2,
            min_atr_mult=Decimal("0.60"))
        self.assertEqual((high, low, direction), (Decimal(105), Decimal(95), "down"))
        self.assertEqual(size_atr, Decimal(5))
        self.assertTrue(confirmed)


if __name__ == "__main__":
    unittest.main()
