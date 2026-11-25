"""Provider-neutral market-data event contracts.

The contracts deliberately use :class:`~decimal.Decimal` for prices and quantities so
normalization never introduces binary floating-point rounding into persisted data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any


UTC = timezone.utc


def ensure_utc(value: datetime) -> datetime:
    """Return an aware UTC timestamp, rejecting naive timestamps."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must include a timezone")
    return value.astimezone(UTC)


def epoch_millis_to_utc(value: int | float | str) -> datetime:
    """Convert epoch milliseconds to an aware UTC timestamp."""

    try:
        numeric = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid epoch timestamp: {value!r}") from exc
    # Binance uses milliseconds for trades. Supporting microseconds here makes the
    # contract safer for future providers without changing the stored time model.
    divisor = 1_000_000 if abs(numeric) >= 100_000_000_000_000 else 1_000
    return datetime.fromtimestamp(numeric / divisor, tz=UTC)


def decimal_value(value: Decimal | str | int | float, field_name: str) -> Decimal:
    """Parse a positive decimal value without accepting floats, NaN, or infinity."""

    if isinstance(value, float):
        raise ValueError(f"{field_name} must not be supplied as a float")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field_name} must be a decimal: {value!r}") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError(f"{field_name} must be finite and positive")
    return parsed


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


@dataclass(frozen=True, slots=True)
class RawTradeEvent:
    """A trade event parsed from a provider, before instrument mapping."""

    venue_id: str
    venue_symbol: str
    event_time: datetime
    received_time: datetime
    price: Decimal
    quantity: Decimal
    venue_trade_id: str | None = None
    sequence_no: int | None = None
    aggressor_side: str = "unknown"
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.venue_id or not self.venue_symbol:
            raise ValueError("venue_id and venue_symbol are required")
        object.__setattr__(self, "event_time", ensure_utc(self.event_time))
        object.__setattr__(self, "received_time", ensure_utc(self.received_time))
        object.__setattr__(self, "price", decimal_value(self.price, "price"))
        object.__setattr__(self, "quantity", decimal_value(self.quantity, "quantity"))
        if self.aggressor_side not in {"buy", "sell", "unknown"}:
            raise ValueError("aggressor_side must be buy, sell, or unknown")
        if self.sequence_no is not None and self.sequence_no < 0:
            raise ValueError("sequence_no cannot be negative")

    @property
    def deduplication_key(self) -> str:
        """Build the strongest stable identity available for this source event."""

        if self.venue_trade_id is not None:
            identity = f"trade:{self.venue_id}:{self.venue_trade_id}"
        elif self.sequence_no is not None:
            identity = f"sequence:{self.venue_id}:{self.venue_symbol}:{self.sequence_no}"
        else:
            digest = hashlib.sha256(_canonical_json(self.payload)).hexdigest()
            identity = f"payload:{self.venue_id}:{self.venue_symbol}:{digest}"
        return identity

    def normalize(self, canonical_symbol: str, source_object_id: str, source_run_id: str | None = None) -> "NormalizedTrade":
        return NormalizedTrade(
            venue_id=self.venue_id,
            venue_symbol=self.venue_symbol,
            canonical_symbol=canonical_symbol,
            event_time=self.event_time,
            received_time=self.received_time,
            price=self.price,
            quantity=self.quantity,
            venue_trade_id=self.venue_trade_id,
            sequence_no=self.sequence_no,
            deduplication_key=self.deduplication_key,
            aggressor_side=self.aggressor_side,
            source_object_id=source_object_id,
            source_run_id=source_run_id,
            payload=self.payload,
        )


@dataclass(frozen=True, slots=True)
class NormalizedTrade:
    """Canonical trade record ready for archive and database writers."""

    venue_id: str
    venue_symbol: str
    canonical_symbol: str
    event_time: datetime
    received_time: datetime
    price: Decimal
    quantity: Decimal
    deduplication_key: str
    source_object_id: str
    venue_trade_id: str | None = None
    sequence_no: int | None = None
    aggressor_side: str = "unknown"
    source_run_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.canonical_symbol or not self.source_object_id:
            raise ValueError("canonical_symbol and source_object_id are required")
        object.__setattr__(self, "event_time", ensure_utc(self.event_time))
        object.__setattr__(self, "received_time", ensure_utc(self.received_time))
        object.__setattr__(self, "price", decimal_value(self.price, "price"))
        object.__setattr__(self, "quantity", decimal_value(self.quantity, "quantity"))
        if not self.deduplication_key:
            raise ValueError("deduplication_key is required")

    def as_archive_row(self) -> dict[str, Any]:
        """Return a serialization-safe row for Parquet writers."""

        return {
            "venue_id": self.venue_id,
            "venue_symbol": self.venue_symbol,
            "canonical_symbol": self.canonical_symbol,
            "event_time": self.event_time,
            "received_time": self.received_time,
            "price": str(self.price),
            "quantity": str(self.quantity),
            "venue_trade_id": self.venue_trade_id,
            "sequence_no": self.sequence_no,
            "deduplication_key": self.deduplication_key,
            "aggressor_side": self.aggressor_side,
            "source_object_id": self.source_object_id,
            "source_run_id": self.source_run_id,
            "payload_json": json.dumps(self.payload, sort_keys=True, default=str),
        }
