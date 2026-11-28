"""Fail-closed instrument and data-quality gates."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Sequence
from app.feeds import Candle

@dataclass(frozen=True, slots=True)
class DataQualityGate:
    stale_threshold_seconds: float
    def evaluate(self, candles: Sequence[Candle], now: datetime) -> tuple[bool, tuple[str,...]]:
        if not candles:
            return False,("no_market_data",)
        latest=max(c.bar_end for c in candles)
        stale=(now.astimezone(latest.tzinfo)-latest).total_seconds()
        return (stale<=self.stale_threshold_seconds, () if stale<=self.stale_threshold_seconds else ("data_stale",))

@dataclass(frozen=True, slots=True)
class LiquidityGate:
    min_price: Decimal = Decimal("5")
    min_avg_volume: Decimal = Decimal("100000")
    max_spread_bps: Decimal = Decimal("50")
    def evaluate(self, candles: Sequence[Candle]) -> tuple[bool, tuple[str,...]]:
        if not candles:
            return False,("liquidity_data_unavailable",)
        recent=list(candles)[-20:]
        avg=sum((c.volume for c in recent),Decimal(0))/Decimal(len(recent))
        if avg<self.min_avg_volume:
            return False,("liquidity_volume_failed",)
        if recent[-1].close<self.min_price:
            return False,("liquidity_price_failed",)
        return True,()
