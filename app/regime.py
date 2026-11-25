"""Provider-neutral market regime interface."""

from __future__ import annotations

from enum import Enum
from typing import Protocol


class Regime(str, Enum):
    NEWS_DAY = "NEWS_DAY"
    CHOP_DAY = "CHOP_DAY"
    TREND_DAY = "TREND_DAY"


class RegimeProvider(Protocol):
    def regime_at(self, timestamp: object, symbol: str) -> Regime: ...


class StaticRegimeProvider:
    """Deterministic provider for tests and replay; no network dependency."""

    def __init__(self, regime: Regime = Regime.CHOP_DAY) -> None:
        self.regime = regime

    def regime_at(self, timestamp: object, symbol: str) -> Regime:
        return self.regime
