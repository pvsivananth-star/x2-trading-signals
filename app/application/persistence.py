"""Phase 7 persistence boundaries and in-memory reference adapters.

Production deployments can replace these adapters with Postgres/object storage without
changing domain or strategy code.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, Sequence

from app.domain.event_envelope import EventEnvelope


class OperationalStateStore(Protocol):
    def put(self, key: str, value: Any) -> None: ...
    def get(self, key: str) -> Any | None: ...
    def delete(self, key: str) -> None: ...


class MarketHistoryStore(Protocol):
    def append(self, instrument: str, timeframe: str, bars: Sequence[Any]) -> None: ...
    def query(self, instrument: str, timeframe: str) -> Sequence[Any]: ...


class AuditEventStore(Protocol):
    def append(self, event: EventEnvelope) -> None: ...
    def by_correlation(self, correlation_id: str) -> Sequence[EventEnvelope]: ...
    def by_aggregate(self, aggregate_id: str) -> Sequence[EventEnvelope]: ...


class BacktestArtifactStore(Protocol):
    def save(self, run_id: str, artifact: Any) -> None: ...
    def get(self, run_id: str) -> Any | None: ...
    def list(self) -> Sequence[str]: ...


@dataclass
class InMemoryOperationalStateStore:
    values: dict[str, Any]

    def __init__(self) -> None:
        self.values = {}

    def put(self, key: str, value: Any) -> None:
        self.values[key] = value

    def get(self, key: str) -> Any | None:
        return self.values.get(key)

    def delete(self, key: str) -> None:
        self.values.pop(key, None)


class InMemoryMarketHistoryStore:
    def __init__(self) -> None:
        self._bars: dict[tuple[str, str], list[Any]] = {}

    def append(self, instrument: str, timeframe: str, bars: Sequence[Any]) -> None:
        self._bars.setdefault((instrument, timeframe), []).extend(bars)

    def query(self, instrument: str, timeframe: str) -> Sequence[Any]:
        return tuple(self._bars.get((instrument, timeframe), ()))


class InMemoryAuditEventStore:
    def __init__(self) -> None:
        self._events: dict[str, EventEnvelope] = {}

    def append(self, event: EventEnvelope) -> None:
        if event.event_id in self._events:
            return
        self._events[event.event_id] = event

    def by_correlation(self, correlation_id: str) -> Sequence[EventEnvelope]:
        return tuple(e for e in self._events.values() if e.correlation_id == correlation_id)

    def by_aggregate(self, aggregate_id: str) -> Sequence[EventEnvelope]:
        return tuple(e for e in self._events.values() if e.aggregate_id == aggregate_id)


class InMemoryBacktestArtifactStore:
    def __init__(self) -> None:
        self._artifacts: dict[str, Any] = {}

    def save(self, run_id: str, artifact: Any) -> None:
        if not run_id:
            raise ValueError("run_id is required")
        self._artifacts[run_id] = artifact

    def get(self, run_id: str) -> Any | None:
        return self._artifacts.get(run_id)

    def list(self) -> Sequence[str]:
        return tuple(sorted(self._artifacts))


@dataclass(frozen=True, slots=True)
class LineageRecord:
    correlation_id: str
    event_ids: tuple[str, ...]
    first_seen: datetime
    last_seen: datetime


class LineageIndex:
    """Read-side lineage index; event store remains the source of truth."""

    def __init__(self, store: AuditEventStore) -> None:
        self.store = store

    def trace(self, correlation_id: str) -> LineageRecord | None:
        events = tuple(self.store.by_correlation(correlation_id))
        if not events:
            return None
        ordered = sorted(events, key=lambda e: e.occurred_at)
        return LineageRecord(
            correlation_id,
            tuple(e.event_id for e in ordered),
            ordered[0].occurred_at,
            ordered[-1].occurred_at,
        )
