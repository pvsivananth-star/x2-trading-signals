"""Minimal calendar boundary; exchange-specific calendars belong in adapters."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import UTC, datetime, time
@dataclass(frozen=True, slots=True)
class SessionWindow:
    open_at: datetime
    close_at: datetime
    def __post_init__(self) -> None:
        if self.open_at.tzinfo is None or self.close_at.tzinfo is None:
            raise ValueError("session timestamps must be timezone-aware")
        if self.close_at < self.open_at:
            raise ValueError("session close cannot precede open")
class WeekendCalendar:
    def is_open(self, venue: str, at: datetime) -> bool:
        return at.astimezone(UTC).weekday() < 5
    def session_bounds(self, venue: str, at: datetime) -> tuple[datetime, datetime]:
        at=at.astimezone(UTC)
        return (datetime.combine(at.date(),time.min,tzinfo=UTC),
                datetime.combine(at.date(),time.max,tzinfo=UTC))
