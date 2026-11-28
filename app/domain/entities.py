"""Dependency-free immutable domain entities.

These are intentionally small. Rich strategy behavior belongs in application
services/strategy implementations, not in these value objects.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Instrument:
    symbol: str
    asset_class: str
    exchange: str | None = None


@dataclass(frozen=True, slots=True)
class Bar:
    instrument: Instrument
    timeframe: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        if self.high < self.low:
            raise ValueError("bar high cannot be below bar low")
        if self.open < self.low or self.open > self.high:
            raise ValueError("bar open must be inside high/low range")
        if self.close < self.low or self.close > self.high:
            raise ValueError("bar close must be inside high/low range")
        if self.volume < 0:
            raise ValueError("bar volume cannot be negative")


@dataclass(frozen=True, slots=True)
class Decision:
    decision: str
    reason_codes: tuple[str, ...]
    timestamp: datetime
    strategy_id: str | None = None
    portfolio_id: str | None = None

    def __post_init__(self) -> None:
        if not self.decision:
            raise ValueError("decision is required")
        if not self.reason_codes:
            raise ValueError("at least one reason code is required")
