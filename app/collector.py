"""Provider-neutral live collector foundation; deliberately opens no sockets."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum

from app.domain.events import RawTradeEvent


class CollectorState(str, Enum):
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    STALE = "stale"
    TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class CollectorCheckpoint:
    event_time: datetime | None = None
    sequence: int | None = None
    venue_event_id: str | None = None


class InMemoryCheckpointStore:
    """A restart-persistent checkpoint store; replace with disk/DB in adapters."""

    def __init__(self) -> None:
        self.checkpoint = CollectorCheckpoint()
        self.accepted_keys: set[str] = set()

    def load(self) -> CollectorCheckpoint:
        return self.checkpoint

    def save(self, checkpoint: CollectorCheckpoint) -> None:
        self.checkpoint = checkpoint

    def remember(self, deduplication_key: str) -> None:
        self.accepted_keys.add(deduplication_key)


@dataclass(frozen=True, slots=True)
class CollectorHealth:
    state: CollectorState
    last_event: datetime | None
    last_sequence: int | None
    venue_event_id: str | None
    error_count: int
    lag_seconds: float
    stale: bool


class LiveCollector:
    def __init__(
        self,
        *,
        stale_seconds: float = 30.0,
        initial_backoff_seconds: float = 0.1,
        max_backoff_seconds: float = 10.0,
        max_retries: int = 5,
        checkpoint_store: InMemoryCheckpointStore | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if stale_seconds < 0 or initial_backoff_seconds < 0 or max_backoff_seconds < 0:
            raise ValueError("collector thresholds and backoff must be non-negative")
        self.stale_seconds = stale_seconds
        self.initial_backoff_seconds = initial_backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.max_retries = max_retries
        self.store = checkpoint_store or InMemoryCheckpointStore()
        self.clock = clock
        self.checkpoint = self.store.load()
        self._accepted = set(getattr(self.store, "accepted_keys", set()))
        self.state = CollectorState.DISCONNECTED
        self.error_count = 0
        self._lock = threading.RLock()
        self._stopped = False

    def start(self) -> None:
        with self._lock:
            self._stopped = False
            self.state = CollectorState.CONNECTING

    def reconnect(self, failures: Iterable[Exception] = ()) -> float:
        """Record failures and return the next bounded backoff; terminal at max_retries."""
        with self._lock:
            if self._stopped:
                return 0.0
            items = list(failures)
            self.error_count += len(items)
            attempts = min(self.error_count, self.max_retries)
            if attempts >= self.max_retries:
                self.state = CollectorState.TERMINAL
                return 0.0
            delay = min(self.initial_backoff_seconds * (2 ** max(attempts - 1, 0)), self.max_backoff_seconds)
            self.state = CollectorState.DISCONNECTED
            return delay

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            if self.state != CollectorState.TERMINAL:
                self.state = CollectorState.DISCONNECTED

    def restart(self) -> None:
        self.start()

    def accept(self, event: RawTradeEvent) -> bool:
        """Atomically accept an event once, update checkpoint, and persist it."""
        with self._lock:
            if self._stopped or self.state == CollectorState.TERMINAL:
                return False
            key = event.deduplication_key
            if key in self._accepted:
                return False
            self._accepted.add(key)
            self.checkpoint = CollectorCheckpoint(
                event_time=event.event_time,
                sequence=event.sequence_no,
                venue_event_id=event.venue_trade_id,
            )
            self.store.save(self.checkpoint)
            if hasattr(self.store, "remember"):
                self.store.remember(key)
            self.state = CollectorState.CONNECTED
            return True

    def replay(self, events: Iterable[RawTradeEvent]) -> int:
        accepted = 0
        for event in sorted(events, key=lambda item: (item.event_time, item.sequence_no if item.sequence_no is not None else -1)):
            accepted += int(self.accept(event))
        return accepted

    @property
    def stale(self) -> bool:
        if self.checkpoint.event_time is None:
            return True
        lag = (self.clock() - self.checkpoint.event_time).total_seconds()
        return lag > self.stale_seconds

    @property
    def blocks_signals_and_orders(self) -> bool:
        return self.stale or self.state in {CollectorState.STALE, CollectorState.TERMINAL, CollectorState.DISCONNECTED}

    def health(self) -> CollectorHealth:
        lag = 0.0
        if self.checkpoint.event_time is not None:
            lag = max(0.0, (self.clock() - self.checkpoint.event_time).total_seconds())
        state = CollectorState.STALE if self.stale and self.state != CollectorState.TERMINAL else self.state
        return CollectorHealth(state, self.checkpoint.event_time, self.checkpoint.sequence,
                              self.checkpoint.venue_event_id, self.error_count, lag, lag > self.stale_seconds)
