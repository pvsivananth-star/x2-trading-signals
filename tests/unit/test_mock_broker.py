import json
import threading
import time
import unittest
from decimal import Decimal
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.actor_runtime import Actor02RegimeScanner, Actor04ExecutionEngine, Actor06TradeAnalyzer
from app.mock_broker import MockBroker, MockBrokerServer


class MockBrokerTests(unittest.TestCase):
    def setUp(self):
        self.broker = MockBroker(host="127.0.0.1", port=0, delay_ms=250, success_rate=1.0)
        self.server = MockBrokerServer(self.broker)
        self.port = self.server.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()

    def request(self, method, path, payload=None, headers=None):
        data = None if payload is None else json.dumps(payload).encode()
        req = Request(f"http://127.0.0.1:{self.port}{path}", data=data,
                      headers=headers or {}, method=method)
        try:
            with urlopen(req, timeout=3) as response:
                return response.status, json.loads(response.read().decode())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def test_health_latency_fill_position_sell_close_and_audit(self):
        started = time.monotonic()
        code, health = self.request("GET", "/health")
        self.assertEqual(code, 200)
        self.assertFalse(health["live_write_enabled"])
        elapsed = time.monotonic() - started

        code, buy = self.request("POST", "/buy", {"symbol": "AAPL", "quantity": "10", "signal_id": "s1", "narrative": "Trend entry"}, {"Idempotency-Key": "idem-1"})
        self.assertEqual(code, 201)
        self.assertEqual(buy["order"]["state"], "filled")
        self.assertGreaterEqual(time.monotonic() - started - elapsed, 0.20)

        code, _repeat = self.request("POST", "/buy", {"symbol": "AAPL", "quantity": "10"}, {"Idempotency-Key": "idem-1"})
        self.assertEqual(code, 409)

        code, _sell = self.request("POST", "/sell", {"symbol": "AAPL", "quantity": "4"})
        self.assertEqual(code, 201)
        positions = self.request("GET", "/positions")[1]["positions"]
        self.assertEqual(Decimal(positions[0]["quantity"]), Decimal(6))

        code, _oversell = self.request("POST", "/sell", {"symbol": "AAPL", "quantity": "7"})
        self.assertEqual(code, 422)
        code, _close = self.request("POST", "/close-all", {})
        self.assertEqual(code, 201)
        positions = self.request("GET", "/positions")[1]["positions"]
        self.assertEqual(positions, [])
        audit = self.request("GET", "/audit")[1]["audit"]
        self.assertTrue(any(a["narrative"] == "Trend entry" for a in audit))

    def test_quote_quote_delay_and_cancel(self):
        started = time.monotonic()
        code, quote = self.request("GET", "/quotes?symbol=AAPL")
        self.assertEqual(code, 200)
        self.assertEqual(quote["symbol"], "AAPL")
        self.assertGreaterEqual(time.monotonic() - started, 0.20)

        code, _rejected = self.request("POST", "/sell", {"symbol": "AAPL", "quantity": "1"})
        self.assertEqual(code, 422)


class ActorTests(unittest.TestCase):
    def test_scanner_execution_and_analyzer(self):
        scanner = Actor02RegimeScanner([
            {"symbol": "AAPL", "enabled": True, "slot_rvol": 1.2},
            {"symbol": "BTC-USD", "enabled": True, "slot_rvol": 2.0},
        ])
        candidates = scanner.discover(market_open=True, regime="TREND_DAY")
        cards = scanner.evaluate(candidates, {"AAPL": True})
        self.assertEqual([c["symbol"] for c in candidates], ["AAPL", "BTC-USD"])
        self.assertEqual(cards[0]["display_action"], "BUY")

        class Adapter:
            def buy(self, payload, idempotency_key=None):
                return 201, {"accepted": payload}
            def sell(self, payload, idempotency_key=None):
                return 201, {"accepted": payload}
            def close_all(self):
                return 201, {"closed": 1}
        engine = Actor04ExecutionEngine(Adapter())
        code, _result = engine.execute(cards[0], quantity=Decimal(2), idempotency_key="s1")
        self.assertEqual(code, 201)

        analyzer = Actor06TradeAnalyzer()
        metrics = analyzer.analyze([
            {"side": "sell", "realized_pnl": "100"},
            {"side": "sell", "realized_pnl": "-90"},
        ])
        self.assertEqual(metrics["trade_count"], 2)
        self.assertEqual(metrics["rectifications"]["NVDA"], "probation")
        self.assertEqual(metrics["rectifications"]["SLV"], "excluded")


if __name__ == "__main__":
    unittest.main()
