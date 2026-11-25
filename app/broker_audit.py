"""Audit persistence protocols for the local mock broker."""
from __future__ import annotations

import json
from collections.abc import Mapping
from threading import RLock
from typing import Protocol


class BrokerAuditRepository(Protocol):
    def save_quote(self, quote: Mapping[str, object]) -> None: ...
    def save_trade(self, trade: Mapping[str, object]) -> None: ...
    def save_order(self, order: Mapping[str, object]) -> None: ...
    def save_fill(self, fill: Mapping[str, object]) -> None: ...
    def save_position(self, position: Mapping[str, object]) -> None: ...
    def save_audit(self, event: Mapping[str, object]) -> None: ...

    def quotes(self) -> tuple[dict[str, object], ...]: ...
    def trades(self) -> tuple[dict[str, object], ...]: ...
    def orders(self) -> tuple[dict[str, object], ...]: ...
    def fills(self) -> tuple[dict[str, object], ...]: ...
    def positions(self) -> tuple[dict[str, object], ...]: ...
    def audit(self) -> tuple[dict[str, object], ...]: ...


class MemoryBrokerAuditRepository:
    """Thread-safe in-memory audit store for unit tests and demo sessions."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._quotes: list[dict[str, object]] = []
        self._trades: list[dict[str, object]] = []
        self._orders: list[dict[str, object]] = []
        self._fills: list[dict[str, object]] = []
        self._positions: list[dict[str, object]] = []
        self._audit: list[dict[str, object]] = []

    @staticmethod
    def _copy(value: Mapping[str, object]) -> dict[str, object]:
        return json.loads(json.dumps(value, default=str))

    def _append(self, target: str, value: Mapping[str, object]) -> None:
        with self._lock:
            getattr(self, target).append(self._copy(value))

    def save_quote(self, quote: Mapping[str, object]) -> None: self._append("_quotes", quote)
    def save_trade(self, trade: Mapping[str, object]) -> None: self._append("_trades", trade)
    def save_order(self, order: Mapping[str, object]) -> None: self._append("_orders", order)
    def save_fill(self, fill: Mapping[str, object]) -> None: self._append("_fills", fill)
    def save_position(self, position: Mapping[str, object]) -> None:
        item = self._copy(position)
        with self._lock:
            self._positions = [p for p in self._positions if p.get("symbol") != item.get("symbol")]
            self._positions.append(item)
    def save_audit(self, event: Mapping[str, object]) -> None: self._append("_audit", event)

    def quotes(self) -> tuple[dict[str, object], ...]:
        with self._lock: return tuple(self._quotes)
    def trades(self) -> tuple[dict[str, object], ...]:
        with self._lock: return tuple(self._trades)
    def orders(self) -> tuple[dict[str, object], ...]:
        with self._lock: return tuple(self._orders)
    def fills(self) -> tuple[dict[str, object], ...]:
        with self._lock: return tuple(self._fills)
    def positions(self) -> tuple[dict[str, object], ...]:
        with self._lock: return tuple(self._positions)
    def audit(self) -> tuple[dict[str, object], ...]:
        with self._lock: return tuple(self._audit)


class PostgresBrokerAuditRepository:
    """Optional PostgreSQL audit repository. No credentials or secrets are stored."""

    def __init__(self, database_url: str) -> None:
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("PostgreSQL audit requires psycopg[binary]") from exc
        self._connection = psycopg.connect(database_url, autocommit=False)

    def close(self) -> None:
        self._connection.close()

    def _execute(self, table: str, columns: tuple[str, ...], value: Mapping[str, object], conflict: str | None = None) -> None:
        # Only a fixed allow-list is accepted, preventing table/column injection.
        allowed = {"mock_quotes", "mock_trades", "mock_orders", "mock_fills", "mock_positions", "mock_broker_audit"}
        if table not in allowed:
            raise ValueError("invalid audit table")
        placeholders = ", ".join(["%s"] * len(columns))
        names = ", ".join(columns)
        sql = f"INSERT INTO trading.{table} ({names}) VALUES ({placeholders})"
        if conflict:
            sql += f" ON CONFLICT {conflict}"
        params = tuple(value.get(column) for column in columns)
        if table in {"mock_trades", "mock_broker_audit"}:
            import psycopg.types.json

            params = tuple(psycopg.types.json.Jsonb(item) if name in {"reasons", "payload"} else item
                           for name, item in zip(columns, params, strict=True))
        with self._connection.cursor() as cursor:
            cursor.execute(sql, params)
        self._connection.commit()

    def save_quote(self, quote: Mapping[str, object]) -> None:
        self._execute("mock_quotes", ("symbol", "bid", "ask", "last", "timestamp"), quote)

    def save_trade(self, trade: Mapping[str, object]) -> None:
        self._execute("mock_trades", ("trade_id", "order_id", "signal_id", "symbol", "side", "quantity", "price", "narrative", "reasons", "timestamp"), trade)

    def save_order(self, order: Mapping[str, object]) -> None:
        cols = ("order_id", "client_order_id", "signal_id", "symbol", "side", "quantity", "price", "state", "narrative", "reason", "submitted_at")
        self._execute("mock_orders", cols, order)

    def save_fill(self, fill: Mapping[str, object]) -> None:
        cols = ("fill_id", "order_id", "trade_id", "symbol", "side", "quantity", "price", "timestamp")
        self._execute("mock_fills", cols, fill)

    def save_position(self, position: Mapping[str, object]) -> None:
        cols = ("symbol", "quantity", "average_cost", "current_value", "unrealized_pnl", "updated_at")
        sql = "INSERT INTO trading.mock_positions (" + ", ".join(cols) + ") VALUES (" + ", ".join(["%s"] * len(cols)) + ") ON CONFLICT (symbol) DO UPDATE SET quantity=EXCLUDED.quantity, average_cost=EXCLUDED.average_cost, current_value=EXCLUDED.current_value, unrealized_pnl=EXCLUDED.unrealized_pnl, updated_at=EXCLUDED.updated_at"
        with self._connection.cursor() as cursor:
            cursor.execute(sql, tuple(position.get(column) for column in cols))
        self._connection.commit()

    def save_audit(self, event: Mapping[str, object]) -> None:
        cols = ("event_id", "order_id", "signal_id", "event", "narrative", "reasons", "timestamp")
        self._execute("mock_broker_audit", cols, event)

    def _rows(self, table: str, order_by: str) -> tuple[dict[str, object], ...]:
        allowed = {"mock_quotes", "mock_trades", "mock_orders", "mock_fills", "mock_positions", "mock_broker_audit"}
        if table not in allowed:
            raise ValueError("invalid audit table")
        with self._connection.cursor() as cursor:
            cursor.execute(f"SELECT * FROM trading.{table} ORDER BY {order_by}")
            names = [description.name for description in cursor.description]
            return tuple(dict(zip(names, row, strict=True)) for row in cursor.fetchall())

    def quotes(self) -> tuple[dict[str, object], ...]: return self._rows("mock_quotes", "timestamp")
    def trades(self) -> tuple[dict[str, object], ...]: return self._rows("mock_trades", "timestamp")
    def orders(self) -> tuple[dict[str, object], ...]: return self._rows("mock_orders", "submitted_at")
    def fills(self) -> tuple[dict[str, object], ...]: return self._rows("mock_fills", "timestamp")
    def positions(self) -> tuple[dict[str, object], ...]: return self._rows("mock_positions", "symbol")
    def audit(self) -> tuple[dict[str, object], ...]: return self._rows("mock_broker_audit", "timestamp")
