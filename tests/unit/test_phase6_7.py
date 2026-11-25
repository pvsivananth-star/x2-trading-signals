import unittest
from datetime import UTC, datetime
from decimal import Decimal

from app.backtest.bars import Bar, Trade
from app.backtest.replay import (
    ReplayClock,
    ReplayRun,
    calculate_metrics,
    chronological_split,
    walk_forward_splits,
)
from app.backtest.simulator import OrderSimulator, SimulationConfig
from app.features import FeatureSnapshot
from app.policy import TradingPolicy
from app.regime import Regime
from app.strategy import ConfluenceV1

MARKETS = {
    "engine": {
        "mode": "demo",
        "base_currency": "USD",
        "pre_close_entry_cutoff_mins": 60,
        "mandatory_pre_close_flat_mins": 15,
        "regime_rules": {
            "NEWS_DAY": {"allow_new_entries": False},
            "CHOP_DAY": {"allow_new_entries": True, "max_risk_pct": 0.0008},
            "TREND_DAY": {
                "allow_new_entries": True,
                "a_tier_risk_pct": 0.0025,
                "b_tier_risk_pct": 0.0017,
            },
        },
    },
    "markets": {
        "US_EQUITIES": {
            "tier": "TIER_A",
            "enabled": True,
            "core_symbols": ["AAPL", "SPY"],
            "slot_rvol_min": 1.05,
            "min_net_moveroom_pct": 0.0045,
        },
        "HONGKONG_HKEX": {"tier": "TIER_D_DISABLED", "enabled": False, "core_symbols": ["0700.HK"]},
        "CRYPTO_MAJORS": {"tier": "TIER_B", "enabled": True, "allocation_pct": 0.05,
                           "broker_route": "DEMO_OR_IBKR", "core_symbols": ["BTC-USD"],
                           "slot_rvol_min": 1.15, "min_net_moveroom_pct": 0.0065},
    },
}
STRATEGY = {
    "strategy": {
        "id": "confluence_pullback_v1",
        "enabled": True,
        "allowed_regimes": ["TREND_DAY", "CHOP_DAY"],
        "timeframes": {"discovery_mins": 30, "swing_mins": 15, "signal_mins": 5},
    }
}


def snapshot() -> FeatureSnapshot:
    now = datetime(2026, 11, 20, tzinfo=UTC)
    return FeatureSnapshot(
        now,
        Decimal(100),
        Decimal(2),
        Decimal("1.2"),
        Decimal(105),
        Decimal(95),
        Decimal(98),
        Decimal(100),
        Decimal(102),
        "up",
        "higher-highs-higher-lows",
        Decimal(20),
    )


class Phase67Tests(unittest.TestCase):
    def test_policy_and_strategy_entry_and_deterministic_card(self):
        policy = TradingPolicy(MARKETS, "policy-test")
        engine = ConfluenceV1(STRATEGY, policy)
        card1 = engine.evaluate(
            timestamp=datetime(2026, 11, 20, tzinfo=UTC),
            symbol="AAPL",
            snapshot=snapshot(),
            regime=Regime.TREND_DAY,
            stale_feed_seconds=1,
            stale_threshold_seconds=10,
            minutes_to_close=120,
        )
        card2 = engine.evaluate(
            timestamp=datetime(2026, 11, 20, tzinfo=UTC),
            symbol="AAPL",
            snapshot=snapshot(),
            regime=Regime.TREND_DAY,
            stale_feed_seconds=1,
            stale_threshold_seconds=10,
            minutes_to_close=120,
        )
        self.assertEqual(card1.action, "enter_long")
        self.assertEqual(card1.signal_id, card2.signal_id)
        self.assertEqual(card1.dedupe_key, card2.dedupe_key)
        self.assertEqual(card1.selected_fib_zone, "CORE_382_618")
        self.assertEqual(card1.display_action, "BUY")
        self.assertIn("RVOL", card1.reason)
        self.assertIn("move_room", card1.parameters)

    def test_negative_move_room_rejects_buy(self):
        policy = TradingPolicy(MARKETS, "policy-test")
        engine = ConfluenceV1(STRATEGY, policy)
        negative = FeatureSnapshot(
            snapshot().timestamp, Decimal(100), Decimal(2), Decimal("1.2"),
            Decimal(105), Decimal(95), Decimal(98), Decimal(100), Decimal(102),
            "up", "higher-highs-higher-lows", Decimal("-0.10"),
        )
        card = engine.evaluate(
            timestamp=negative.timestamp,
            symbol="AAPL",
            snapshot=negative,
            regime=Regime.TREND_DAY,
            stale_feed_seconds=1,
            stale_threshold_seconds=10,
            minutes_to_close=120,
        )
        self.assertEqual(card.action, "hold")
        self.assertEqual(card.display_action, "HOLD")
        self.assertIn("insufficient_move_room", card.rejected_gates)

    def test_negative_policy_gates(self):
        policy = TradingPolicy(MARKETS, "policy-test")
        engine = ConfluenceV1(STRATEGY, policy)
        cases = [
            ("0700.HK", Regime.TREND_DAY, 1, True, 120),
            ("AAPL", Regime.NEWS_DAY, 1, True, 120),
            ("AAPL", Regime.TREND_DAY, 20, True, 120),
            ("AAPL", Regime.TREND_DAY, 1, False, 120),
            ("AAPL", Regime.TREND_DAY, 1, True, 30),
            ("UNKNOWN", Regime.TREND_DAY, 1, True, 120),
        ]
        for symbol, regime, stale, session, minutes in cases:
            card = engine.evaluate(
                timestamp=datetime(2026, 11, 20, tzinfo=UTC),
                symbol=symbol,
                snapshot=snapshot(),
                regime=regime,
                stale_feed_seconds=stale,
                stale_threshold_seconds=10,
                session_open=session,
                minutes_to_close=minutes,
            )
            self.assertNotEqual(card.action, "enter_long")
            self.assertTrue(card.rejected_gates)

    def test_btc_tier_b_buy_and_sell_matrix(self):
        policy = TradingPolicy(MARKETS, "policy-test")
        engine = ConfluenceV1(STRATEGY, policy)
        now = datetime(2026, 11, 20, tzinfo=UTC)
        up = FeatureSnapshot(now, Decimal(100), Decimal(2), Decimal("1.5"),
                             Decimal(105), Decimal(95), Decimal(100), Decimal(101),
                             Decimal(102), "up", "higher-highs-higher-lows", Decimal("0.008"))
        buy = engine.evaluate(timestamp=now, symbol="BTC-USD", snapshot=up,
                              regime=Regime.TREND_DAY, stale_feed_seconds=1,
                              stale_threshold_seconds=10, minutes_to_close=120)
        self.assertEqual(buy.display_action, "BUY")

        down = FeatureSnapshot(now, Decimal(100), Decimal(2), Decimal("1.5"),
                               Decimal(105), Decimal(95), Decimal(98), Decimal(100),
                               Decimal(102), "down", "lower-highs-lower-lows", Decimal("-0.008"))
        sell = engine.evaluate(timestamp=now, symbol="BTC-USD", snapshot=down,
                               regime=Regime.TREND_DAY, stale_feed_seconds=1,
                               stale_threshold_seconds=10, minutes_to_close=120,
                               position_quantity=Decimal(1))
        self.assertEqual(sell.display_action, "SELL")

    def test_false_positive_filters(self):
        policy = TradingPolicy(MARKETS, "policy-test")
        engine = ConfluenceV1(STRATEGY, policy)
        now = datetime(2026, 11, 20, tzinfo=UTC)
        cases = [
            FeatureSnapshot(now, Decimal(100), Decimal(2), Decimal("1.04"), Decimal(105), Decimal(95), Decimal(98), Decimal(100), Decimal(102), "up", "mixed", Decimal("0.008")),
            FeatureSnapshot(now, Decimal(100), Decimal(2), Decimal("1.20"), Decimal(105), Decimal(95), Decimal(98), Decimal(100), Decimal(102), "sideways", "range", Decimal("0.008")),
            FeatureSnapshot(now, Decimal(100), Decimal(2), Decimal("1.20"), Decimal(105), Decimal(95), Decimal(98), Decimal(100), Decimal(102), "up", "higher-highs-higher-lows", Decimal("-0.008")),
        ]
        for snapshot_item in cases:
            card = engine.evaluate(timestamp=now, symbol="AAPL", snapshot=snapshot_item,
                                   regime=Regime.TREND_DAY, stale_feed_seconds=1,
                                   stale_threshold_seconds=10, minutes_to_close=120)
            self.assertEqual(card.display_action, "HOLD")

    def test_disabled_and_feed_controls(self):
        policy = TradingPolicy(MARKETS, "policy-test")
        engine = ConfluenceV1(STRATEGY, policy)
        now = datetime(2026, 11, 20, tzinfo=UTC)
        controls = [
            ("0700.HK", 1, True, 120),
            ("AAPL", 20, True, 120),
            ("AAPL", 1, False, 120),
            ("AAPL", 1, True, 30),
        ]
        for symbol, stale, session_open, minutes in controls:
            card = engine.evaluate(timestamp=now, symbol=symbol, snapshot=snapshot(),
                                   regime=Regime.TREND_DAY, stale_feed_seconds=stale,
                                   stale_threshold_seconds=10, session_open=session_open,
                                   minutes_to_close=minutes)
            self.assertEqual(card.display_action, "HOLD")
            self.assertTrue(card.rejected_gates)

    def test_exit_gate_and_unknown_status(self):
        policy = TradingPolicy(MARKETS, "policy-test")
        engine = ConfluenceV1(STRATEGY, policy)
        down = FeatureSnapshot(
            snapshot().timestamp,
            Decimal(100),
            Decimal(2),
            Decimal(1),
            Decimal(105),
            Decimal(95),
            Decimal(98),
            Decimal(100),
            Decimal(102),
            "down",
            "lower-highs-lower-lows",
            Decimal(-2),
        )
        card = engine.evaluate(
            timestamp=down.timestamp,
            symbol="AAPL",
            snapshot=down,
            regime=Regime.TREND_DAY,
            stale_feed_seconds=1,
            stale_threshold_seconds=10,
            position_quantity=Decimal(1),
            minutes_to_close=120,
        )
        self.assertEqual(card.action, "exit_long")
        self.assertEqual(card.display_action, "SELL")
        self.assertIn("turned down", card.reason)

    def test_replay_clock_split_walk_forward_and_gap_policy(self):
        now = datetime(2026, 11, 20, tzinfo=UTC)
        events = [
            Trade(now + __import__("datetime").timedelta(minutes=i), Decimal(100 + i), Decimal(1))
            for i in range(4)
        ]
        clock = ReplayClock(events)
        self.assertEqual(clock.next(), events[0])
        self.assertEqual(len(chronological_split(events, 0.5, 0.25)[0]), 2)
        self.assertEqual(len(walk_forward_splits(events, 2, 1)), 2)
        with self.assertRaises(RuntimeError):
            ReplayRun(gap_policy="fail", quality_threshold=0).run(
                events, {}, quality_findings=(object(),)
            )

    def test_simulator_market_limit_partial_reject(self):
        bar = Bar(
            datetime(2026, 11, 20, tzinfo=UTC),
            datetime(2026, 11, 20, 0, 5, tzinfo=UTC),
            Decimal(100),
            Decimal(105),
            Decimal(95),
            Decimal(100),
            Decimal(10),
            1,
        )
        simulator = OrderSimulator(
            SimulationConfig(partial_fill_ratio=Decimal("0.5"), fee_rate=Decimal("0.001"))
        )
        partial = simulator.submit(bar, "buy", "market", Decimal(10))
        self.assertEqual(partial.status, "partial")
        self.assertEqual(partial.quantity, Decimal(5))
        self.assertEqual(
            simulator.submit(bar, "buy", "limit", Decimal(1), Decimal(90)).status, "rejected"
        )
        self.assertEqual(simulator.submit(bar, "bogus", "market", Decimal(1)).status, "rejected")

    def test_golden_metrics_and_deterministic_replay(self):
        now = datetime(2026, 11, 20, tzinfo=UTC)
        events = [Trade(now, Decimal(100), Decimal(1)), Trade(now, Decimal(101), Decimal(1))]
        first = ReplayRun(interval_seconds=300).run(events, {"seed": 7}, {"fixture": "golden"})
        second = ReplayRun(interval_seconds=300).run(events, {"seed": 7}, {"fixture": "golden"})
        self.assertEqual(first, second)
        metrics = calculate_metrics(
            ((now, Decimal(100)), (now, Decimal(90))), trade_pnl=(Decimal(10), Decimal(-5))
        )
        self.assertEqual(metrics["profit_factor"], Decimal(2))
        self.assertEqual(metrics["max_drawdown"], Decimal("0.1"))


if __name__ == "__main__":
    unittest.main()
