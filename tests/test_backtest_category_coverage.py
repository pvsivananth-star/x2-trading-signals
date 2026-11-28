"""Backtest coverage for warmup and every fixture market category."""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from app.backtest.bars import Bar
from app.backtest.dataset_runner import DEFAULT_PROFILE, run_symbol
from app.backtest.preload import preload_directory
from app.features import FeatureSnapshot, causal_slot_rvol
from app.costs import load_costs
from app.policy import TradingPolicy
from app.regime import Regime
from app.strategy import ConfluenceV1


CATEGORY_SYMBOLS = {
    "equity": "AAPL",
    "etf": "SPY",
    "index": "SPX",
    "gold": "GC=F",
    "oil": "CL=F",
    "crypto": "BTC-USD",
}


def trade_ready_snapshot(timestamp: datetime, close: Decimal = Decimal("100")) -> FeatureSnapshot:
    return FeatureSnapshot(
        timestamp=timestamp,
        vwap=Decimal("99"),
        atr=Decimal("2"),
        rvol=Decimal("1.20"),
        swing_high=Decimal("112"),
        swing_low=Decimal("88"),
        fib_382=Decimal("103"),
        fib_500=Decimal("100"),
        fib_618=Decimal("97"),
        trend="up",
        structure="higher-highs-higher-lows",
        move_room=Decimal("0.06"),
        last_close=close,
        pullback_depth=Decimal("0.50"),
        structure_target=Decimal("110"),
        structure_source="SWING_HIGH",
        pullback_depth_short=Decimal("0.50"),
        structure_target_short=Decimal("90"),
        structure_source_short="SWING_LOW",
        structure_target_continuation=Decimal("110"),
        structure_source_continuation="SWING_CONTINUATION",
        impulse_rvol=Decimal("1.20"),
        session_efficiency=Decimal("0.60"),
        context_regime=Regime.TREND_DAY.value,
        context_timestamp=timestamp,
        swing_15m_high=Decimal("112"),
        swing_15m_low=Decimal("88"),
        swing_15m_direction="up",
        swing_15m_size_atr=Decimal("1.0"),
        swing_15m_confirmed=True,
        rolling_rvol=Decimal("1.20"),
        slot_rvol_samples=5,
    )


class BacktestCategoryCoverageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo = "pvsivananth-star/x2-trading-signals"
        cls.markets = yaml.safe_load(Path("config/markets_17.yaml").read_text())
        cls.policy = TradingPolicy(cls.markets, "dataset-run")
        cls.strategy = ConfluenceV1.from_yaml("config/strategies/confluence_pullback.yaml", cls.policy)
        cls.costs = load_costs("config/costs.yaml")

    def test_all_categories_are_trade_eligible_in_backtest_mode(self) -> None:
        now = datetime(2026, 11, 24, 15, 0, tzinfo=UTC)
        previous = trade_ready_snapshot(now - timedelta(minutes=5), Decimal("99"))
        current = trade_ready_snapshot(now, Decimal("100"))

        for category, symbol in CATEGORY_SYMBOLS.items():
            with self.subTest(category=category, symbol=symbol):
                decision = self.strategy.evaluate(
                    timestamp=now,
                    symbol=symbol,
                    snapshot=current,
                    previous_snapshot=previous,
                    regime=Regime.TREND_DAY,
                    stale_feed_seconds=0,
                    stale_threshold_seconds=300,
                    minutes_to_close=120,
                    live_mode=False,
                )
                self.assertEqual(
                    decision.action,
                    "enter_long",
                    f"{category}/{symbol} blocked by {decision.rejected_gates}",
                )

    def test_seven_sessions_supply_five_prior_slot_samples(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            start = datetime(2026, 11, 16, 14, 30, tzinfo=UTC)
            path = root / "SPY.csv"
            with path.open("w", encoding="utf-8") as handle:
                handle.write("timestamp,open,high,low,close,volume\\n")
                for day in range(7):
                    session = start + timedelta(days=day)
                    for slot in range(2):
                        ts = session + timedelta(minutes=5 * slot)
                        handle.write(
                            f"{ts.isoformat()},{100+slot},{101+slot},{99+slot},{100+slot},{1000+day}\\n"
                        )
            loaded = preload_directory(root, 300)
            bars = loaded.grouped[(1, 300)]
            value, samples = causal_slot_rvol(
                bars,
                bars[-1].bar_end,
                session_timezone="America/New_York",
                session_open=None,
                session_close=None,
                lookback_sessions=10,
                min_sessions=5,
            )
            self.assertEqual(samples, 6)
            self.assertIsNotNone(value)
            self.assertEqual(loaded.summary["symbols_loaded"], 1)

    def test_runner_can_create_a_trade_for_each_category(self) -> None:
        timestamps = [
            datetime(2026, 11, 24, 14, 35, tzinfo=UTC),
            datetime(2026, 11, 24, 14, 40, tzinfo=UTC),
            datetime(2026, 11, 24, 14, 45, tzinfo=UTC),
        ]
        bars = [
            Bar(ts, ts + timedelta(minutes=5), Decimal("100"), Decimal("101"),
                Decimal("99"), Decimal("100"), Decimal("1000"), 10)
            for ts in timestamps
        ]

        class TradeReadyEngine:
            stop_mult = Decimal("1")
            min_rr = Decimal("1.5")
            roundtrip_cost = Decimal("0.001")
            slippage_buffer = Decimal("0.0005")
            strategy = {"execution": {"partial_fill_ratio": "1"}}

            def evaluate(self, **kwargs):
                if kwargs["position_quantity"] == 0:
                    return SimpleNamespace(
                        action="enter_long",
                        parameters={
                            "target": "108",
                            "target_source": "STRUCTURE",
                            "size_weight": "1",
                            "stop_mult": "1",
                        },
                        rejected_gates=(),
                        selected_fib_zone="CORE_382_618",
                    )
                return SimpleNamespace(
                    action="exit_long",
                    parameters={},
                    rejected_gates=(),
                    selected_fib_zone="TARGET_HIT",
                )

        for category, symbol in CATEGORY_SYMBOLS.items():
            with self.subTest(category=category, symbol=symbol):
                result = run_symbol(
                    symbol,
                    bars,
                    DEFAULT_PROFILE[symbol],
                    TradeReadyEngine(),
                    self.policy,
                    self.costs,
                    Decimal("10000"),
                    Decimal("0.0025"),
                    allow_estimates=True,
                )
                self.assertGreaterEqual(
                    len(result["trades"]),
                    1,
                    f"{category}/{symbol} produced no trade: {result['decision_diagnostics']}",
                )


if __name__ == "__main__":
    unittest.main()
