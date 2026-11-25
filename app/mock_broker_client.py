"""Stdlib HTTP adapter for the loopback mock broker."""
from __future__ import annotations

import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class MockBrokerClient:
    def __init__(self, base_url: str = "http://127.0.0.1:9090", timeout: float = 5.0) -> None:
        if not base_url.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]")):
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
        except Exception as exc:
            if hasattr(exc, "code"):
                code = int(exc.code)
                body = json.loads(exc.read().decode())
            else:
                raise
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
