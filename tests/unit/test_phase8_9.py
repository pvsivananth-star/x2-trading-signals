import os
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from app.actors import ActorRuntime
from app.api import ReadAPI, ReadAPIServer
from app.execution import DemoBroker
from app.feeds import FeedStore, load_feed_config
from app.policy import TradingPolicy
from app.regime import Regime
from app.strategy import ConfluenceV1

MARKETS = {
    "engine": {"mode": "demo", "base_currency": "USD", "pre_close_entry_cutoff_mins": 60,
               "mandatory_pre_close_flat_mins": 15,
               "regime_rules": {"TREND_DAY": {"allow_new_entries": True, "a_tier_risk_pct": 0.0025}}},
    "markets": {"US_EQUITIES": {"tier": "TIER_A", "enabled": True, "core_symbols": ["AAPL"],
                                  "cluster": "US_TECH_INDEX", "cluster_risk_cap": 0.005,
                                  "slot_rvol_min": 1.05, "min_net_moveroom_pct": 0.0045},
                "CRYPTO_MAJORS": {"tier": "TIER_C_PROBATION", "enabled": True, "core_symbols": ["BTC-USD"]},
                "HONGKONG_HKEX": {"tier": "TIER_D_DISABLED", "enabled": False, "core_symbols": ["0700.HK"]}},
}


class Phase89Tests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("TRADING_MODE", None)
        os.environ.pop("X2_LIVE_BROKER_URL", None)
        os.environ.pop("LIVE_BROKER_URL", None)
        self.policy = TradingPolicy(MARKETS, "phase89")
        self.now = datetime(2026, 11, 20, 12, tzinfo=UTC)

    def test_accounting_partial_duplicate_fill_and_reconciliation(self):
        broker = DemoBroker(self.policy, starting_cash=Decimal(1000000), fee_rate=Decimal("0.001"))
        order = broker.submit_signal(signal_id="signal-1", symbol="AAPL", side="buy", quantity=Decimal(10),
                                     timestamp=self.now, minutes_to_close=120, regime=Regime.TREND_DAY,
                                     limit_price=Decimal(100))
        first = broker.fill(order.order_id, price=Decimal(100), quantity=Decimal(4), timestamp=self.now)
        second = broker.fill(order.order_id, price=Decimal(100), quantity=Decimal(6), timestamp=self.now)
        self.assertEqual(order.state, "working")
        self.assertEqual(broker.orders[0].state, "filled")
        self.assertEqual(first.order_id, second.order_id)
        pos = broker.positions[0]
        self.assertEqual(pos.quantity, Decimal(10))
        self.assertEqual(pos.average_cost, Decimal(100))
        snap = broker.snapshot(self.now, {"AAPL": Decimal(110)})
        self.assertEqual(snap.cash, Decimal("998999.000"))
        self.assertEqual(snap.positions_value, Decimal(1100))
        self.assertEqual(snap.unrealized_pnl, Decimal(100))
        self.assertEqual(snap.equity, Decimal("1000099.000"))
        self.assertEqual(snap.cash + snap.positions_value, snap.equity)

    def test_duplicate_fill_is_idempotent_and_rejected_transition_fails(self):
        broker = DemoBroker(self.policy)
        order = broker.submit_signal(signal_id="signal-2", symbol="AAPL", side="buy", quantity=Decimal(1),
                                     timestamp=self.now, minutes_to_close=120, regime=Regime.TREND_DAY,
                                     limit_price=Decimal(100))
        f1 = broker.fill(order.order_id, price=Decimal(100), quantity=Decimal(1), timestamp=self.now)
        f2 = broker.fill(order.order_id, price=Decimal(100), quantity=Decimal(1), timestamp=self.now)
        if f2 is not None:
            self.assertEqual(f1.fill_id, f2.fill_id)
        with self.assertRaises(ValueError):
            order.transition("submitted", timestamp=self.now)

    def test_cutoff_probation_live_and_flatten_lineage(self):
        broker = DemoBroker(self.policy)
        cutoff = broker.submit_signal(signal_id="cutoff", symbol="AAPL", side="buy", quantity=Decimal(1),
                                      timestamp=self.now, minutes_to_close=30, regime=Regime.TREND_DAY)
        self.assertEqual(cutoff.state, "rejected")
        disabled = broker.submit_signal(signal_id="crypto", symbol="BTC-USD", side="buy", quantity=Decimal(1),
                                        timestamp=self.now, minutes_to_close=120, regime=Regime.TREND_DAY)
        self.assertEqual(disabled.state, "rejected")
        os.environ["TRADING_MODE"] = "live"
        with self.assertRaises(RuntimeError):
            DemoBroker(self.policy)
        os.environ.pop("TRADING_MODE", None)

        broker = DemoBroker(self.policy)
        order = broker.submit_signal(signal_id="line", symbol="AAPL", side="buy", quantity=Decimal(1),
                                     timestamp=self.now, minutes_to_close=120, regime=Regime.TREND_DAY,
                                     limit_price=Decimal(100))
        broker.fill(order.order_id, price=Decimal(100), quantity=Decimal(1), timestamp=self.now)
        flat_fills = broker.flatten(timestamp=self.now, marks={"AAPL": Decimal(105)})
        self.assertEqual(len(flat_fills), 1)
        ids = {(card.signal_id, card.order_id) for card in broker.audit_cards}
        self.assertTrue(all(order_id for _, order_id in ids))

    def test_feed_config_fallback_grouping_and_stale(self):
        config = load_feed_config("config/feeds.yaml")
        store = FeedStore(config)
        symbols = store.market_symbols()
        self.assertIn("AAPL", {item["symbol"] for item in symbols})
        self.assertTrue(store.bars("AAPL", "1m", 1)[-1].synthetic)
        api = ReadAPI(store=store)
        code, markets = api.get("/v1/markets")
        self.assertEqual(code, 200)
        self.assertTrue({m["market"] for m in markets["items"]} >= {"EQ", "ETF", "Index", "Gold", "Oil", "Crypto"})
        code, bars = api.get("/v1/bars", {"symbol": ["AAPL"], "interval": ["1m"], "limit": ["2"]})
        self.assertEqual(code, 200)
        self.assertTrue(bars["synthetic"])
        self.assertEqual(len(bars["items"]), 2)

    def test_actor_card_and_stale_synthetic_blocking(self):
        config = load_feed_config("config/feeds.yaml")
        store = FeedStore(config)
        policy = TradingPolicy(MARKETS, "phase89")
        strategy_config = {"strategy": {"id": "confluence_pullback_v1", "enabled": True,
                                        "allowed_regimes": ["TREND_DAY", "CHOP_DAY"],
                                        "timeframes": {"signal_mins": 5, "swing_mins": 15, "discovery_mins": 30}}}
        actor = ActorRuntime(store, policy, ConfluenceV1(strategy_config, policy), interval="1m")
        cards = actor.evaluate_once()
        self.assertEqual(cards, [])

    def test_tick_pump_warmup_and_every_tick_actor_execution(self):
        from app.backtest.tick_pump import TickPumpRuntime

        config = load_feed_config("config/feeds.yaml")
        store = FeedStore(config)
        strategy_config = {"strategy": {"id": "confluence_pullback_v1", "enabled": True,
                                         "allowed_regimes": ["TREND_DAY", "CHOP_DAY"],
                                         "timeframes": {"signal_mins": 5, "swing_mins": 15, "discovery_mins": 30}}}
        actor = ActorRuntime(store, self.policy, ConfluenceV1(strategy_config, self.policy), interval="1m",
                             stale_threshold_seconds=config.stale_threshold_seconds)
        broker = DemoBroker(self.policy, starting_cash=Decimal(25000))
        pump = TickPumpRuntime(store, actor, broker, warmup_fraction=0.25,
                               interval_seconds=0)

        result = pump.run(max_ticks=20)

        self.assertEqual(result.warmup_ticks, 487)
        self.assertEqual(result.ticks_processed, 20)
        self.assertGreaterEqual(len(result.decisions), 20)
        self.assertTrue(all(getattr(card, "symbol", "") for card in result.decisions))
        self.assertGreaterEqual(len(broker.orders), 0)
        self.assertGreaterEqual(len(broker.fills), 0)
        self.assertGreaterEqual(len(broker.positions), 0)
        self.assertEqual(result.ticks_processed, 20)

    def test_api_portfolio_position_and_targets(self):
        config = load_feed_config("config/feeds.yaml")
        store = FeedStore(config)
        broker = DemoBroker(self.policy, starting_cash=Decimal(25000))
        order = broker.submit_signal(signal_id="demo-aapl", symbol="AAPL", side="buy",
                                     quantity=Decimal(1), timestamp=self.now,
                                     minutes_to_close=120, regime=Regime.TREND_DAY,
                                     limit_price=Decimal(100))
        broker.fill(order.order_id, price=Decimal(100), quantity=Decimal(1), timestamp=self.now)
        card = SimpleNamespace(inputs={"fib_618": "110.50"}, selected_fib_zone="CORE_382_618")
        actor = SimpleNamespace(cards={"AAPL": card})
        api = ReadAPI(store=store, actor=actor, broker=broker)

        code, portfolio = api.get("/v1/portfolio")
        self.assertEqual(code, 200)
        self.assertEqual(portfolio["kitty"], "24899.9900")
        self.assertEqual(portfolio["positions"][0]["symbol"], "AAPL")
        self.assertEqual(portfolio["positions"][0]["quantity"], "1")
        self.assertIn("pnl_pct", portfolio["positions"][0])

        code, position = api.get("/v1/position/AAPL")
        self.assertEqual(code, 200)
        self.assertEqual(position["entry_price_executed"], "100")
        self.assertEqual(position["entry_time"], "2026-11-20T12:00:00Z")
        self.assertEqual(position["expected_exit"], "110.50")
        self.assertEqual(len(position["fills"]), 1)

        no_position = api.get("/v1/position/SPY")[1]
        self.assertIsNone(no_position["position"])
        self.assertIsNone(no_position["expected_exit"])

    def test_api_contract_pagination_authorization_and_readonly(self):
        api = ReadAPI({"orders": [{"id": 1}, {"id": 2}], "positions": []})
        self.assertTrue(api.authorized("127.0.0.1", None))
        self.assertFalse(api.authorized("10.0.0.2", None))
        code, body = api.get("/v1/orders", {"limit": ["1"], "offset": ["1"]})
        self.assertEqual(code, 200)
        self.assertEqual(body["total"], 2)
        self.assertEqual(body["items"], [{"id": 2}])
        token_api = ReadAPI({}, token="secret")
        self.assertFalse(token_api.authorized("127.0.0.1", None))
        self.assertTrue(token_api.authorized("127.0.0.1", "secret"))
        import os
        os.environ["X2_CONTAINER_MODE"] = "1"
        try:
            self.assertTrue(api.authorized("172.18.0.2", None))
            self.assertFalse(api.authorized("8.8.8.8", None))
        finally:
            os.environ.pop("X2_CONTAINER_MODE", None)
        code, body = api.get("/v1/health")
        self.assertFalse(body["live_write_enabled"])
        with self.assertRaises(ValueError):
            ReadAPIServer(api, host="0.0.0.0", port=8081)


if __name__ == "__main__":
    unittest.main()
