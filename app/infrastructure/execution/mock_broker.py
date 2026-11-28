"""ExecutionPort adapter for the loopback mock broker; never a live broker."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.application.execution import BrokerOrderUpdate
from app.domain.orders.model import Order, OrderReplacement, OrderStatus
from app.mock_broker_client import MockBrokerClient


class MockBrokerExecutionAdapter:
    def __init__(self, client: MockBrokerClient | None = None):
        self.client = client or MockBrokerClient()

    def submit(self, order: Order, idempotency_key: str) -> BrokerOrderUpdate:
        code, body = self.client.order({
            "symbol": order.symbol,
            "side": order.side.lower(),
            "quantity": str(order.quantity),
            "order_type": order.order_type.lower(),
            "limit_price": str(order.limit_price) if order.limit_price is not None else None,
            "client_order_id": order.client_order_id,
        }, idempotency_key)
        return self._update(order, code, body)

    def cancel(self, order: Order, idempotency_key: str) -> BrokerOrderUpdate:
        code, body = self.client.cancel(order.broker_order_id or order.order_id)
        return self._update(order, code, body, default_status=OrderStatus.CANCELLED)

    def replace(self, order: Order, replacement: OrderReplacement, idempotency_key: str) -> BrokerOrderUpdate:
        payload = {
            "order_id": order.broker_order_id or order.order_id,
            "quantity": str(replacement.quantity) if replacement.quantity is not None else None,
            "limit_price": str(replacement.limit_price) if replacement.limit_price is not None else None,
        }
        code, body = self.client._request("POST", "/replace", payload, idempotency_key)
        return self._update(order, code, body, default_status=OrderStatus.WORKING)

    def reconcile(self) -> tuple[BrokerOrderUpdate, ...]:
        code, body = self.client.orders()
        if code != 200:
            raise RuntimeError(f"mock broker reconciliation failed: {code}")
        updates = []
        for item in body.get("orders", []):
            status = self._status(str(item.get("status", "UNKNOWN")))
            updates.append(BrokerOrderUpdate(
                order_id=str(item.get("client_order_id") or item.get("order_id")),
                status=status,
                timestamp=datetime.fromisoformat(str(item["timestamp"]).replace("Z", "+00:00")),
                broker_order_id=str(item.get("order_id")) if item.get("order_id") else None,
                filled_quantity=Decimal(str(item.get("filled_quantity", 0))),
                average_fill_price=Decimal(str(item["average_fill_price"])) if item.get("average_fill_price") else None,
                reject_reason=item.get("reject_reason"),
            ))
        return tuple(updates)

    def _update(self, order: Order, code: int, body: dict[str, object], *, default_status: OrderStatus = OrderStatus.WORKING) -> BrokerOrderUpdate:
        if code >= 500:
            raise RuntimeError("mock broker unavailable")
        status = self._status(str(body.get("status", default_status.value)), default_status)
        return BrokerOrderUpdate(
            order_id=order.order_id,
            status=status,
            timestamp=order.updated_at,
            broker_order_id=str(body.get("order_id")) if body.get("order_id") else order.broker_order_id,
            filled_quantity=Decimal(str(body.get("filled_quantity", order.filled_quantity))),
            average_fill_price=Decimal(str(body["average_fill_price"])) if body.get("average_fill_price") else order.average_fill_price,
            reject_reason=str(body.get("error")) if code >= 400 else None,
        )

    @staticmethod
    def _status(value: str, default: OrderStatus = OrderStatus.UNKNOWN) -> OrderStatus:
        value = value.upper()
        aliases = {
            "SUBMITTED": OrderStatus.SUBMITTED, "ACCEPTED": OrderStatus.ACCEPTED,
            "WORKING": OrderStatus.WORKING, "OPEN": OrderStatus.WORKING,
            "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED, "PARTIAL": OrderStatus.PARTIALLY_FILLED,
            "FILLED": OrderStatus.FILLED, "CANCELLED": OrderStatus.CANCELLED,
            "CANCELED": OrderStatus.CANCELLED, "REJECTED": OrderStatus.REJECTED,
        }
        return aliases.get(value, default)
