"""Provider-neutral, fail-closed event-risk assessment contract for strategy entry gates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class EventRiskAssessment:
    """A point-in-time event-risk state supplied by a calendar/news adapter.

    Providers must return clear only after checking the relevant instrument and
    decision-time window. Missing, stale, future-dated, or unknown assessments fail closed
    whenever the strategy marks the event gate as required.
    """

    state: str
    as_of: datetime
    reason: str = ""
    source: str = "unspecified"

    def __post_init__(self) -> None:
        if self.state not in {"clear", "blocked", "unknown"}:
            raise ValueError("event-risk state must be clear, blocked, or unknown")
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("event-risk as_of timestamp must be timezone-aware")

    def is_fresh_at(self, timestamp: datetime, max_age_seconds: int) -> bool:
        if timestamp.tzinfo is None or timestamp.utcoffset() is None or max_age_seconds <= 0:
            return False
        age = (timestamp - self.as_of).total_seconds()
        return 0 <= age <= max_age_seconds
