"""Clock implementations for live operation and deterministic replay."""
from __future__ import annotations
from datetime import UTC, datetime
from typing import Iterable, Iterator
from .ports import ClockPort
class SystemClock(ClockPort):
    def now(self) -> datetime:
        return datetime.now(UTC)
class ReplayClock(ClockPort):
    def __init__(self, timestamps: Iterable[datetime]) -> None:
        self._timestamps: Iterator[datetime] = iter(timestamps)
        self._current: datetime | None = None
    def advance(self) -> datetime:
        value = next(self._timestamps)
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("replay timestamps must be timezone-aware")
        value = value.astimezone(UTC)
        if self._current is not None and value < self._current:
            raise ValueError("replay timestamps must be monotonic")
        self._current = value
        return value
    def now(self) -> datetime:
        if self._current is None:
            raise RuntimeError("replay clock has not advanced")
        return self._current
