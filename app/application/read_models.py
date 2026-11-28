"""Stable v1 API read models.

These are intentionally separate from domain entities so UI/API evolution does not
leak transport concerns into the trading core.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any


API_VERSION = "v1"


def json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat().replace("+00:00", "Z")
    if hasattr(value, "value") and not isinstance(value, (str, bytes)):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {k: json_value(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(v) for v in value]
    return value


@dataclass(frozen=True, slots=True)
class Page:
    items: tuple[Any, ...]
    total: int
    limit: int
    offset: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_version": API_VERSION,
            "items": json_value(self.items),
            "total": self.total,
            "limit": self.limit,
            "offset": self.offset,
        }


@dataclass(frozen=True, slots=True)
class SignalReadModel:
    signal_id: str
    symbol: str
    portfolio_id: str
    strategy_id: str
    stage: str
    side: str
    score: int
    reason_codes: tuple[str, ...]
    as_of: str
    config_version: str

    def to_dict(self) -> dict[str, Any]:
        return {"api_version": API_VERSION, **json_value(self)}


@dataclass(frozen=True, slots=True)
class OrderReadModel:
    order_id: str
    signal_id: str
    symbol: str
    side: str
    quantity: str
    state: str
    submitted_at: str
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"api_version": API_VERSION, **json_value(self)}


@dataclass(frozen=True, slots=True)
class FillReadModel:
    fill_id: str
    order_id: str
    signal_id: str
    symbol: str
    side: str
    quantity: str
    price: str
    fee: str
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return {"api_version": API_VERSION, **json_value(self)}


@dataclass(frozen=True, slots=True)
class RiskReadModel:
    status: str
    reason_codes: tuple[str, ...]
    kill_switch: str
    data_healthy: bool
    broker_healthy: bool
    reconciled: bool

    def to_dict(self) -> dict[str, Any]:
        return {"api_version": API_VERSION, **json_value(self)}
