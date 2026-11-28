"""Canonical event envelope used across replay, simulation and execution paths."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Mapping
from uuid import UUID, uuid4

def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)

@dataclass(frozen=True, slots=True)
class EventEnvelope:
    event_id: UUID
    event_type: str
    event_version: int
    occurred_at: datetime
    observed_at: datetime
    causal_cutoff: datetime
    correlation_id: UUID
    causation_id: UUID | None
    aggregate_id: str
    producer: str
    producer_version: str
    provenance: Mapping[str, str] = field(default_factory=dict)
    portfolio_id: str | None = None
    strategy_id: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.event_type:
            raise ValueError("event_type is required")
        if self.event_version < 1:
            raise ValueError("event_version must be >= 1")
        if not self.aggregate_id:
            raise ValueError("aggregate_id is required")
        if not self.producer or not self.producer_version:
            raise ValueError("producer and producer_version are required")
        occurred, observed, cutoff = map(_utc, (self.occurred_at, self.observed_at, self.causal_cutoff))
        if cutoff < occurred:
            raise ValueError("causal_cutoff cannot precede occurred_at")
        object.__setattr__(self, "occurred_at", occurred)
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "causal_cutoff", cutoff)
        object.__setattr__(self, "provenance", dict(self.provenance))
        object.__setattr__(self, "payload", dict(self.payload))

    @classmethod
    def create(cls, *, event_type: str, aggregate_id: str, occurred_at: datetime,
               observed_at: datetime, causal_cutoff: datetime, producer: str,
               producer_version: str, correlation_id: UUID | None = None,
               causation_id: UUID | None = None, event_version: int = 1,
               provenance: Mapping[str, str] | None = None,
               portfolio_id: str | None = None, strategy_id: str | None = None,
               payload: Mapping[str, Any] | None = None) -> "EventEnvelope":
        return cls(uuid4(), event_type, event_version, occurred_at, observed_at, causal_cutoff,
                   correlation_id or uuid4(), causation_id, aggregate_id, producer,
                   producer_version, provenance or {}, portfolio_id, strategy_id, payload or {})

    @property
    def idempotency_key(self) -> str:
        return str(self.event_id)
