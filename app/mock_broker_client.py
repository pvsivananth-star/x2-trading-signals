"""Stdlib HTTP adapter for the loopback mock broker."""
from __future__ import annotations

import json
import os
from datetime import datetime
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class MockBrokerClient:
    def __init__(self, base_url: str = "http://127.0.0.1:9090", timeout: float = 5.0) -> None:
        container_mock = os.getenv("X2_CONTAINER_MODE") == "1" and base_url.startswith("http://mock-broker:")
        if not container_mock and not base_url.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]")):
            raise ValueError("Mock broker client must use loopback URL")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, payload: dict[str, object] | None = None,
                 idempotency_key: str | None = None) -> tuple[int, dict[str, object]]:
        data = None if payload is None else json.dumps(payload).encode()
        headers = {"Accept": "application/json", "X-Mock-Broker-Client": "demo-only"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                code = response.status
                body = json.loads(response.read().decode())
        except HTTPError as exc:
            try:
                body = json.loads(exc.read().decode())
            except json.JSONDecodeError:
                body = {"error": "mock_broker_invalid_error_response"}
            return int(exc.code), body
        except URLError:
            return 503, {"error": "mock_broker_unavailable"}
        except OSError:
            return 503, {"error": "mock_broker_unavailable"}
        except json.JSONDecodeError:
            return 502, {"error": "mock_broker_invalid_response"}
        return code, body

    def health(self) -> tuple[int, dict[str, object]]: return self._request("GET", "/health")
    def quote(self, symbol: str) -> tuple[int, dict[str, object]]:
        return self._request("GET", "/quotes?" + urlencode({"symbol": symbol}))
    def buy(self, payload: dict[str, object], idempotency_key: str | None = None) -> tuple[int, dict[str, object]]:
        return self._request("POST", "/buy", payload, idempotency_key)
    def sell(self, payload: dict[str, object], idempotency_key: str | None = None) -> tuple[int, dict[str, object]]:
        return self._request("POST", "/sell", payload, idempotency_key)
    def order(self, payload: dict[str, object], idempotency_key: str | None = None) -> tuple[int, dict[str, object]]:
        return self._request("POST", "/order", payload, idempotency_key)
    def close_all(self) -> tuple[int, dict[str, object]]:
        return self._request("POST", "/close-all", {})
    def cancel(self, order_id: str) -> tuple[int, dict[str, object]]:
        return self._request("POST", "/cancel/" + order_id, {})
    def orders(self) -> tuple[int, dict[str, object]]: return self._request("GET", "/orders")
    def fills(self) -> tuple[int, dict[str, object]]: return self._request("GET", "/fills")
    def positions(self) -> tuple[int, dict[str, object]]: return self._request("GET", "/positions")
    def trades(self) -> tuple[int, dict[str, object]]: return self._request("GET", "/trades")
    def audit(self) -> tuple[int, dict[str, object]]: return self._request("GET", "/audit")


class MockBrokerDashboardAdapter:
    """Read-only dashboard view backed only by the loopback mock-broker HTTP API."""

    def __init__(self, base_url: str = "http://127.0.0.1:9090") -> None:
        self.client = MockBrokerClient(base_url)

    def buy(self, payload: dict[str, object], idempotency_key: str | None = None) -> tuple[int, dict[str, object]]:
        return self.client.buy(payload, idempotency_key)

    def sell(self, payload: dict[str, object], idempotency_key: str | None = None) -> tuple[int, dict[str, object]]:
        return self.client.sell(payload, idempotency_key)

    def close_all(self) -> tuple[int, dict[str, object]]:
        return self.client.close_all()

    def snapshot(self, timestamp: object, marks: dict[str, object]) -> object:
        code, body = self.client.positions()
        positions = body.get("positions", []) if code == 200 else []
        value = Decimal(0)
        unrealized = Decimal(0)
        for position in positions:
            symbol = str(position.get("symbol", ""))
            mark = marks.get(symbol)
            quantity = Decimal(str(position.get("quantity", 0)))
            average = Decimal(str(position.get("average_cost", 0)))
            if mark is not None:
                mark_value = Decimal(str(mark))
                value += mark_value * quantity
                unrealized += (mark_value - average) * quantity
        return type("Snapshot", (), {"cash": Decimal(25000), "positions_value": value,
                                     "equity": Decimal(25000) + value,
                                     "unrealized_pnl": unrealized, "realized_pnl": Decimal(0)})()

    @property
    def positions(self) -> tuple[object, ...]:
        code, body = self.client.positions()
        if code != 200:
            return ()
        result = []
        for item in body.get("positions", []):
            result.append(type("Position", (), {
                "symbol": str(item["symbol"]), "quantity": Decimal(str(item["quantity"])),
                "average_cost": Decimal(str(item["average_cost"])),
            })())
        return tuple(result)

    @property
    def fills(self) -> tuple[object, ...]:
        code, body = self.client.fills()
        if code != 200:
            return ()
        result = []
        for item in body.get("fills", []):
            result.append(type("Fill", (), {
                "symbol": str(item["symbol"]), "side": str(item["side"]),
                "price": Decimal(str(item["price"])), "quantity": Decimal(str(item["quantity"])),
                "timestamp": datetime.fromisoformat(str(item["timestamp"])),
            })())
        return tuple(result)
