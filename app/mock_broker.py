"""Loopback-only HTTP mock broker for demo execution."""
from __future__ import annotations

import hashlib
import json
import os
import random
import time
from datetime import UTC, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import RLock
from urllib.parse import parse_qs, urlparse

from app.broker_audit import BrokerAuditRepository, MemoryBrokerAuditRepository


def utc_text(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def dec(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


class MockBroker:
    """In-memory broker with deterministic accounting and no live routing."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9090,
                 delay_ms: int = 250, success_rate: float = 0.95,
                 audit_repository: BrokerAuditRepository | None = None,
                 quote_seed: int = 1701) -> None:
        if host not in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}:
            raise ValueError("Mock broker host must be loopback-only or container wildcard")
        if host == "0.0.0.0" and os.getenv("X2_CONTAINER_MODE") != "1":
            raise ValueError("Container wildcard host requires X2_CONTAINER_MODE=1")
        self.host, self.port = host, port
        self.delay_ms = delay_ms if delay_ms is not None else 250
        self.success_rate = min(1.0, max(0.0, success_rate))
        self.audit = audit_repository or MemoryBrokerAuditRepository()
        self.quote_seed = quote_seed
        self._lock = RLock()
        self._quotes: dict[str, Decimal] = {}
        self._orders: dict[str, dict[str, object]] = {}
        self._fills: list[dict[str, object]] = []
        self._trades: list[dict[str, object]] = []
        self._positions: dict[str, dict[str, object]] = {}
        self._idempotency: dict[str, dict[str, object]] = {}

    @classmethod
    def from_env(cls) -> MockBroker:
        return cls(
            host=os.getenv("MOCK_BROKER_HOST", "127.0.0.1"),
            port=int(os.getenv("MOCK_BROKER_PORT", "9090")),
            delay_ms=int(os.getenv("MOCK_BROKER_DELAY_MS", "250")),
            success_rate=float(os.getenv("MOCK_BROKER_SUCCESS_RATE", "0.95")),
        )

    def quote(self, symbol: str) -> dict[str, object]:
        symbol = symbol.strip().upper()
        if not symbol:
            raise ValueError("symbol_required")
        with self._lock:
            if symbol not in self._quotes:
                digest = hashlib.sha256(f"{self.quote_seed}:{symbol}".encode()).hexdigest()
                seed = int(digest[:8], 16) % 10000
                last = Decimal(seed + 100) / Decimal(10)
                self._quotes[symbol] = last
            last = self._quotes[symbol]
            bid = (last * Decimal("0.999")).quantize(Decimal("0.01"))
            ask = (last * Decimal("1.001")).quantize(Decimal("0.01"))
            now = datetime.now(UTC)
            result = {"symbol": symbol, "bid": str(bid), "ask": str(ask), "last": str(last), "timestamp": utc_text(now)}
            self.audit.save_quote(result)
            return result

    def _audit(self, event: str, narrative: str, reasons: dict[str, object], order_id: str | None = None,
               signal_id: str | None = None) -> None:
        now = datetime.now(UTC)
        item = {"event_id": hashlib.sha256(f"{now.isoformat()}:{event}:{order_id}:{narrative}".encode()).hexdigest(),
                "order_id": order_id, "signal_id": signal_id, "event": event,
                "narrative": narrative, "reasons": reasons, "timestamp": utc_text(now)}
        self.audit.save_audit(item)

    def submit(self, side: str, payload: dict[str, object], idempotency_key: str | None = None) -> tuple[int, dict[str, object]]:
        time.sleep(max(0, self.delay_ms) / 1000)
        symbol = str(payload.get("symbol", "")).strip().upper()
        if not symbol:
            return 422, {"error": "symbol_required"}
        try:
            quantity = dec(payload["quantity"])
        except (KeyError, ArithmeticError, ValueError):
            return 422, {"error": "quantity_required"}
        if quantity <= 0:
            return 422, {"error": "quantity_must_be_positive"}
        client_key = idempotency_key or str(payload.get("client_order_id") or payload.get("idempotency_key") or "")
        if client_key:
            with self._lock:
                if client_key in self._idempotency:
                    return 409, {"error": "duplicate", "order": self._idempotency[client_key]}
        if side == "sell":
            with self._lock:
                position = self._positions.get(symbol)
                if position is None or dec(position["quantity"]) < quantity:
                    return 422, {"error": "reject_oversell", "reason": "No position or quantity exceeds available position"}

        signal_id = str(payload.get("signal_id") or f"mock:{uuid_hex(symbol, side, quantity)}")
        narrative = str(payload.get("narrative") or f"{side.upper()} {quantity} {symbol} at mock quote")
        q = self.quote(symbol)
        price = dec(payload["price"]) if payload.get("price") is not None else dec(q["ask"] if side == "buy" else q["bid"])
        if price <= 0:
            return 422, {"error": "price_must_be_positive"}
        now = datetime.now(UTC)
        canonical = f"{client_key}:{signal_id}:{symbol}:{side}:{quantity}:{now.isoformat()}"
        order_id = hashlib.sha256(canonical.encode()).hexdigest()[:32]

        if random.random() >= self.success_rate:
            order = {"order_id": order_id, "client_order_id": client_key or None, "signal_id": signal_id,
                     "symbol": symbol, "side": side, "quantity": str(quantity), "price": str(price),
                     "state": "rejected", "narrative": narrative,
                     "reason": "mock_broker_success_probability", "submitted_at": utc_text(now)}
            with self._lock:
                self._orders[order_id] = order
                if client_key:
                    self._idempotency[client_key] = order
            self.audit.save_order(order)
            self._audit("rejected", narrative, {"reason": "mock_broker_success_probability"}, order_id, signal_id)
            return 422, {"error": "rejected", "order": order}

        lifecycle = "accepted"
        order = {"order_id": order_id, "client_order_id": client_key or None, "signal_id": signal_id,
                 "symbol": symbol, "side": side, "quantity": str(quantity), "price": str(price),
                 "state": lifecycle, "narrative": narrative, "reason": None, "submitted_at": utc_text(now)}
        with self._lock:
            self._orders[order_id] = dict(order, state="submitted")
        self.audit.save_order(dict(order, state="submitted"))
        self._audit("submitted", narrative, {"symbol": symbol, "side": side, "quantity": str(quantity)}, order_id, signal_id)

        order["state"] = "accepted"
        with self._lock:
            self._orders[order_id] = dict(order)
        self.audit.save_order(order)
        self._audit("accepted", narrative, {"quote": q}, order_id, signal_id)

        fill_id = hashlib.sha256(f"{order_id}:{now.isoformat()}:full".encode()).hexdigest()
        trade_id = hashlib.sha256(f"trade:{fill_id}".encode()).hexdigest()
        fill = {"fill_id": fill_id, "order_id": order_id, "trade_id": trade_id,
                "symbol": symbol, "side": side, "quantity": str(quantity), "price": str(price), "timestamp": utc_text(now)}
        trade = {"trade_id": trade_id, "order_id": order_id, "signal_id": signal_id,
                 "symbol": symbol, "side": side, "quantity": str(quantity), "price": str(price),
                 "narrative": narrative, "reasons": {"quote": q, "decision": narrative}, "timestamp": utc_text(now)}
        with self._lock:
            self._fills.append(fill)
            self._trades.append(trade)
            pos = self._positions.get(symbol)
            if side == "buy":
                old_qty = dec(pos["quantity"]) if pos else Decimal(0)
                old_avg = dec(pos["average_cost"]) if pos else Decimal(0)
                new_qty = old_qty + quantity
                new_avg = ((old_avg * old_qty) + (price * quantity)) / new_qty
                self._positions[symbol] = {"symbol": symbol, "quantity": str(new_qty), "average_cost": str(new_avg),
                                           "current_value": str(price * new_qty), "unrealized_pnl": str((price - new_avg) * new_qty), "updated_at": utc_text(now)}
            else:
                old_qty = dec(pos["quantity"])
                new_qty = old_qty - quantity
                if new_qty == 0:
                    self._positions.pop(symbol, None)
                else:
                    self._positions[symbol] = {"symbol": symbol, "quantity": str(new_qty), "average_cost": pos["average_cost"],
                                               "current_value": str(price * new_qty), "unrealized_pnl": str((price - dec(pos["average_cost"])) * new_qty), "updated_at": utc_text(now)}
            order["state"] = "filled"
            self._orders[order_id] = dict(order)
            if client_key:
                self._idempotency[client_key] = dict(order)
        self.audit.save_fill(fill)
        self.audit.save_trade(trade)
        self.audit.save_order(order)
        if symbol in self._positions:
            self.audit.save_position(self._positions[symbol])
        else:
            self.audit.save_position({"symbol": symbol, "quantity": "0", "average_cost": "0", "current_value": "0", "unrealized_pnl": "0", "updated_at": utc_text(now)})
        self._audit("filled", narrative, {"fill_id": fill_id, "price": str(price), "quantity": str(quantity)}, order_id, signal_id)
        return 201, {"order": order, "fill": fill, "trade": trade}

    def close_all(self, payload: dict[str, object] | None = None) -> tuple[int, dict[str, object]]:
        time.sleep(max(0, self.delay_ms) / 1000)
        with self._lock:
            symbols = list(self._positions)
        fills: list[dict[str, object]] = []
        for symbol in symbols:
            position = self._positions[symbol]
            quantity = dec(position["quantity"])
            status, result = self.submit("sell", {"symbol": symbol, "quantity": str(quantity),
                                                  "signal_id": "close-all", "narrative": f"Emergency close all {symbol}"})
            if status == 201:
                fills.append(result["fill"])
        return 201, {"closed": len(fills), "fills": fills}

    def cancel(self, order_id: str) -> tuple[int, dict[str, object]]:
        time.sleep(max(0, self.delay_ms) / 1000)
        with self._lock:
            order = self._orders.get(order_id)
            if order is None:
                return 404, {"error": "order_not_found"}
            if order["state"] in {"filled", "cancelled", "rejected"}:
                return 422, {"error": "cannot_cancel", "order": order}
            order = dict(order, state="cancelled", reason="cancelled_by_client")
            self._orders[order_id] = order
        self.audit.save_order(order)
        self._audit("cancelled", "Client cancelled the mock order", {"reason": "cancelled_by_client"}, order_id, str(order.get("signal_id")))
        return 200, {"order": order}

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            positions = tuple(dict(p) for p in self._positions.values())
            orders = tuple(dict(o) for o in self._orders.values())
            return {"quotes": tuple(self.audit.quotes()), "orders": orders,
                    "fills": tuple(dict(f) for f in self._fills), "positions": positions,
                    "trades": tuple(dict(t) for t in self._trades), "audit": tuple(self.audit.audit())}


def uuid_hex(*parts: object) -> str:
    return hashlib.sha256(":".join(str(p) for p in parts).encode()).hexdigest()[:12]


class MockBrokerServer:
    def __init__(self, broker: MockBroker) -> None:
        self.broker = broker
        self.server = ThreadingHTTPServer((broker.host, broker.port), make_handler(broker))

    def serve_forever(self) -> None:
        self.server.serve_forever()

    def shutdown(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def make_handler(broker: MockBroker) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "X2MockBroker/1.0"

        def _send(self, code: int, payload: object) -> None:
            body = json.dumps(payload, sort_keys=True, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Mock-Broker", "demo-only")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self) -> dict[str, object]:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length <= 0:
                return {}
            return json.loads(self.rfile.read(length).decode())

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self._send(200, {"status": "ok", "mode": "demo", "live_write_enabled": False,
                                 "delay_ms": broker.delay_ms, "success_rate": broker.success_rate,
                                 "host": broker.host, "port": broker.port})
                return
            query = parse_qs(parsed.query)
            if parsed.path == "/quotes":
                symbol = query.get("symbol", [""])[0]
                if not symbol:
                    self._send(400, {"error": "symbol_required"})
                    return
                time.sleep(max(0, broker.delay_ms) / 1000)
                self._send(200, broker.quote(symbol))
                return
            snap = broker.snapshot()
            mapping = {"/orders": "orders", "/fills": "fills", "/positions": "positions",
                       "/trades": "trades", "/audit": "audit"}
            key = mapping.get(parsed.path)
            if key:
                self._send(200, {key: list(snap[key])})
                return
            self._send(404, {"error": "not_found"})

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path not in {"/buy", "/sell", "/order", "/close-all"} and not parsed.path.startswith("/cancel/"):
                self._send(404, {"error": "not_found"})
                return
            try:
                payload = self._json()
            except json.JSONDecodeError:
                self._send(422, {"error": "invalid_json"})
                return
            if parsed.path in {"/buy", "/sell"}:
                side = parsed.path[1:]
                key = self.headers.get("Idempotency-Key")
                code, result = broker.submit(side, payload, key)
                self._send(code, result)
                return
            if parsed.path == "/order":
                side = str(payload.get("side", "")).lower()
                if side not in {"buy", "sell"}:
                    self._send(422, {"error": "invalid_side"})
                    return
                key = self.headers.get("Idempotency-Key")
                code, result = broker.submit(side, payload, key)
                self._send(code, result)
                return
            if parsed.path == "/close-all":
                code, result = broker.close_all(payload)
                self._send(code, result)
                return
            order_id = parsed.path.rsplit("/", 1)[-1]
            code, result = broker.cancel(order_id)
            self._send(code, result)

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler
