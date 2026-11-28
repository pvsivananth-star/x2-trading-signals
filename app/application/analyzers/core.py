"""Deterministic analyzers built on the existing causal feature engine."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Mapping, Sequence
from app.features import FeatureSnapshot, feature_snapshot
from app.regime import Regime

@dataclass(frozen=True, slots=True)
class AnalyzerContext:
    symbol: str
    timestamp: datetime
    bars_by_timeframe: Mapping[str, Sequence[object]]
    benchmark_bars: Sequence[object] = ()
    stale_seconds: float = 0.0
    stale_threshold_seconds: float = 300.0

@dataclass(frozen=True, slots=True)
class AnalyzerResult:
    analyzer: str
    timestamp: datetime
    values: Mapping[str, object]
    passed: bool
    reasons: tuple[str, ...]

    @property
    def stale(self) -> bool:
        return self.stale_seconds > self.stale_threshold_seconds if hasattr(self, "stale_seconds") else False

def _snapshot(ctx: AnalyzerContext, tf: str) -> FeatureSnapshot | None:
    bars=list(ctx.bars_by_timeframe.get(tf, ()))
    return feature_snapshot(bars, ctx.timestamp) if bars else None

class TrendAnalyzer:
    def analyze(self, ctx: AnalyzerContext) -> AnalyzerResult:
        snaps={tf:_snapshot(ctx,tf) for tf in ("1W","1d","30m","15m","5m")}
        values={tf:(s.trend if s else "unknown") for tf,s in snaps.items()}
        for tf in ("1W", "1d"):
            bars=[b for b in ctx.bars_by_timeframe.get(tf, ()) if b.bar_end <= ctx.timestamp]
            if len(bars) >= 2:
                values[tf] = "up" if bars[-1].close > bars[0].close else "down" if bars[-1].close < bars[0].close else "sideways"
        lt=values["1W"]; daily=values["1d"]; short=tuple(values[x] for x in ("30m","15m","5m"))
        passed=lt in {"up","down"} or any(x in {"up","down"} for x in short)
        return AnalyzerResult("trend",ctx.timestamp,{"timeframes":values,"long_term":lt,"daily":daily,"short_term":short},passed,() if passed else ("trend_unavailable",))

class VolumeAnalyzer:
    def analyze(self, ctx: AnalyzerContext) -> AnalyzerResult:
        snap=_snapshot(ctx,"5m") or _snapshot(ctx,"15m")
        if snap is None: return AnalyzerResult("volume",ctx.timestamp,{},False,("volume_unavailable",))
        r=snap.rvol; passed=r is not None and r>0
        return AnalyzerResult("volume",ctx.timestamp,{"rvol":r,"impulse_rvol":snap.impulse_rvol},passed,() if passed else ("rvol_unavailable",))

class RelativeStrengthAnalyzer:
    def analyze(self, ctx: AnalyzerContext) -> AnalyzerResult:
        price=_snapshot(ctx,"30m")
        bench=list(ctx.benchmark_bars)
        if price is None or not bench or len(bench)<2:
            return AnalyzerResult("relative_strength",ctx.timestamp,{},False,("benchmark_unavailable",))
        bars=list(ctx.bars_by_timeframe.get("30m",()))
        if len(bars)<2: return AnalyzerResult("relative_strength",ctx.timestamp,{},False,("price_history_unavailable",))
        p0,p1=bars[0].close,bars[-1].close; b0,b1=bench[0].close,bench[-1].close
        if p0<=0 or b0<=0: return AnalyzerResult("relative_strength",ctx.timestamp,{},False,("invalid_benchmark",))
        value=(p1/p0)-(b1/b0)
        return AnalyzerResult("relative_strength",ctx.timestamp,{"excess_return":value},True,())

class StructureAnalyzer:
    def analyze(self, ctx: AnalyzerContext) -> AnalyzerResult:
        snap=_snapshot(ctx,"15m") or _snapshot(ctx,"5m")
        if snap is None: return AnalyzerResult("structure",ctx.timestamp,{},False,("structure_unavailable",))
        return AnalyzerResult("structure",ctx.timestamp,{"structure":snap.structure,"fib_382":snap.fib_382,"fib_500":snap.fib_500,"fib_618":snap.fib_618,"swing_high":snap.swing_high,"swing_low":snap.swing_low,"move_room":snap.move_room},snap.structure!="unknown",() if snap.structure!="unknown" else ("structure_unavailable",))

class DMAAnalyzer:
    """Causal moving-average alignment; returns explicit values rather than a boolean only."""
    def analyze(self, ctx: AnalyzerContext) -> AnalyzerResult:
        bars=list(ctx.bars_by_timeframe.get("30m",()))
        if len(bars)<50: return AnalyzerResult("dma",ctx.timestamp,{},False,("dma_history_unavailable",))
        closes=[b.close for b in bars if b.bar_end<=ctx.timestamp]
        def sma(n:int)->Decimal|None:
            if len(closes)<n:return None
            return sum(closes[-n:],Decimal(0))/Decimal(n)
        fast=sma(20); slow=sma(50)
        if fast is None: return AnalyzerResult("dma",ctx.timestamp,{},False,("dma_history_unavailable",))
        passed=slow is None or fast!=slow
        return AnalyzerResult("dma",ctx.timestamp,{"dma_fast":fast,"dma_slow":slow,"alignment":"bullish" if slow is not None and fast>slow else "bearish" if slow is not None else "insufficient"},passed,() if passed else ("dma_alignment_neutral",))

class MomentumAnalyzer:
    def analyze(self, ctx: AnalyzerContext) -> AnalyzerResult:
        bars=list(ctx.bars_by_timeframe.get("5m",()))
        bars=[b for b in bars if b.bar_end<=ctx.timestamp]
        if len(bars)<4:return AnalyzerResult("momentum",ctx.timestamp,{},False,("momentum_history_unavailable",))
        roc=(bars[-1].close/bars[-4].close)-Decimal(1) if bars[-4].close>0 else Decimal(0)
        return AnalyzerResult("momentum",ctx.timestamp,{"roc":roc},True,())

class RegimeAnalyzer:
    def analyze(self, ctx: AnalyzerContext) -> AnalyzerResult:
        snap=_snapshot(ctx,"30m")
        if snap is None:return AnalyzerResult("regime",ctx.timestamp,{},False,("regime_unavailable",))
        regime=Regime.TREND_DAY if snap.trend in {"up","down"} and snap.structure!="range" else Regime.CHOP_DAY
        return AnalyzerResult("regime",ctx.timestamp,{"regime":regime},True,())
