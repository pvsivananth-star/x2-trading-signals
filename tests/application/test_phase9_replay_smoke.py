from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.application.backtest_replay import ReplayCosts, run_replay
from app.application.strategy_manager import BaseStrategy, StrategyContext, StrategyDecision, StrategySpec
from app.application.signals import SignalStage, SignalSide
from app.backtest.bars import Bar


class AlwaysLongStrategy(BaseStrategy):
    def evaluate(self, context: StrategyContext) -> StrategyDecision:
        return self._decision(
            context,
            side=SignalSide.LONG,
            score=90,
            reasons=("ACCEPTED", "SMOKE"),
            evidence={"causal_cutoff": context.causal_cutoff.isoformat()},
            stage=SignalStage.TRIGGER,
        )


def _bars(count: int = 24) -> list[Bar]:
    start = datetime(2026, 11, 20, 14, 0, tzinfo=UTC)
    return [
        Bar(
            start + timedelta(minutes=5 * i),
            start + timedelta(minutes=5 * (i + 1)),
            Decimal(100 + i),
            Decimal(101 + i),
            Decimal(99 + i),
            Decimal("100.5") + i,
            Decimal("1000"),
            10,
        )
        for i in range(count)
    ]


class Phase9ReplaySmokeTests(unittest.TestCase):
    def test_replay_is_deterministic(self) -> None:
        spec = StrategySpec("SMOKE", "1", "DAILY_TRADING", True, "smoke")
        strategy = AlwaysLongStrategy(spec)
        costs = ReplayCosts(commission_bps=1, spread_bps=1, slippage_bps=1, fee_bps=1, latency_bars=1)
        first = run_replay(_bars(), [strategy], symbol="SIM-EQ", costs=costs)
        second = run_replay(_bars(), [strategy], symbol="SIM-EQ", costs=costs)

        self.assertEqual(first, second)
        self.assertGreaterEqual(len(first.closed_trades), 1)
        self.assertEqual(first.rejected_orders, second.rejected_orders)
        self.assertEqual(first.equity_curve, second.equity_curve)
