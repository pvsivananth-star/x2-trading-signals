"""Causal cutoff and feed-health enforcement shared by analyzers."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Sequence
from app.feeds import Candle

@dataclass(frozen=True, slots=True)
class DataHealth:
    allowed: bool
    stale_seconds: float
    reasons: tuple[str, ...]

class CausalDataGuard:
    def __init__(self, stale_threshold_seconds: float = 300.0) -> None:
        if stale_threshold_seconds < 0:
            raise ValueError("stale threshold cannot be negative")
        self.stale_threshold_seconds = stale_threshold_seconds

    def eligible(self, candles: Sequence[Candle], causal_cutoff: datetime) -> list[Candle]:
        cutoff=causal_cutoff.astimezone(UTC)
        return [c for c in candles if c.bar_end.astimezone(UTC) <= cutoff]

    def health(self, candles: Sequence[Candle], causal_cutoff: datetime) -> DataHealth:
        eligible=self.eligible(candles,causal_cutoff)
        if not eligible:
            return DataHealth(False,float("inf"),("no_causal_data",))
        latest=max(c.bar_end.astimezone(UTC) for c in eligible)
        stale=max(0.0,(causal_cutoff.astimezone(UTC)-latest).total_seconds())
        if stale > self.stale_threshold_seconds:
            return DataHealth(False,stale,("data_stale",))
        return DataHealth(True,stale,())
