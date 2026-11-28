"""Canonical instrument universe and deterministic eligibility gates."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable
from app.feeds import Candle
from .analyzers.gates import LiquidityGate

@dataclass(frozen=True, slots=True)
class InstrumentEligibility:
    symbol: str
    observable: bool
    research: bool
    paper: bool
    live: bool
    reasons: tuple[str, ...]

class InstrumentUniverse:
    def __init__(self, liquidity_gate: LiquidityGate | None = None) -> None:
        self.liquidity_gate = liquidity_gate or LiquidityGate()

    def evaluate(self, symbol: str, candles: Iterable[Candle], *, licensed: bool = True,
                 provider_healthy: bool = True) -> InstrumentEligibility:
        bars=list(candles)
        reasons:list[str]=[]
        observable=bool(bars) and provider_healthy
        if not observable:
            reasons.append("not_observable")
        if not licensed:
            reasons.append("license_not_eligible")
        liquidity_ok, liquidity_reasons=self.liquidity_gate.evaluate(bars)
        if not liquidity_ok:
            reasons.extend(liquidity_reasons)
        research=observable and licensed and liquidity_ok
        return InstrumentEligibility(symbol,observable,research,research,False,tuple(dict.fromkeys(reasons)))
