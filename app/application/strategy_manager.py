"""Isolated strategy managers and deterministic strategy contracts."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Mapping, Protocol, Sequence

from app.application.signals import Signal, SignalManager, SignalSide, SignalStage
from app.domain.reason_codes import ReasonCode


@dataclass(frozen=True, slots=True)
class StrategySpec:
    strategy_id: str
    version: str
    portfolio_id: str
    enabled: bool
    config_version: str


@dataclass(frozen=True, slots=True)
class StrategyContext:
    symbol: str
    timestamp: datetime
    causal_cutoff: datetime
    features: Mapping[str, object]
    analyzers: Mapping[str, object]
    provenance: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StrategyDecision:
    strategy_id: str
    strategy_version: str
    portfolio_id: str
    symbol: str
    timestamp: datetime
    stage: SignalStage
    side: SignalSide
    score: int
    reason_codes: tuple[str, ...]
    evidence: tuple[tuple[str, str], ...]
    signal: Signal

    @property
    def trade_candidate(self) -> bool:
        return self.stage is SignalStage.TRIGGER and self.side is not SignalSide.FLAT


class Strategy(Protocol):
    spec: StrategySpec
    def evaluate(self, context: StrategyContext) -> StrategyDecision: ...


def _value(features: Mapping[str, object], tf: str, name: str, default=None):
    snap = features.get(tf)
    return getattr(snap, name, default) if snap is not None else default


def _decimal(value) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


class BaseStrategy:
    spec: StrategySpec

    def __init__(self, spec: StrategySpec, signal_manager: SignalManager | None = None) -> None:
        self.spec = spec
        self._signals = signal_manager or SignalManager()

    def _decision(self, context: StrategyContext, *, side: SignalSide, score: int,
                  reasons: Sequence[str], evidence: Mapping[str, object],
                  stage: SignalStage) -> StrategyDecision:
        signal = Signal.build(
            symbol=context.symbol, timestamp=context.timestamp, causal_cutoff=context.causal_cutoff,
            stage=stage, side=side, portfolio_id=self.spec.portfolio_id,
            strategy_id=self.spec.strategy_id, strategy_version=self.spec.version,
            config_version=self.spec.config_version, score=score,
            reason_codes=reasons, evidence=evidence, provenance=context.provenance,
        )
        return StrategyDecision(
            self.spec.strategy_id, self.spec.version, self.spec.portfolio_id,
            context.symbol, context.timestamp, stage, side, score,
            tuple(reasons), tuple(sorted((k, str(v)) for k, v in evidence.items())), signal,
        )

    def _no_trade(self, context: StrategyContext, reason: str) -> StrategyDecision:
        return self._decision(
            context, side=SignalSide.FLAT, score=0,
            reasons=(ReasonCode.NO_TRADE.value, reason),
            evidence={"strategy_enabled": self.spec.enabled}, stage=SignalStage.NO_TRADE,
        )


class WeeklyTrendFollowing(BaseStrategy):
    def evaluate(self, context: StrategyContext) -> StrategyDecision:
        if not self.spec.enabled:
            return self._no_trade(context, "strategy_disabled")
        weekly = context.features.get("1W")
        daily = context.features.get("1d")
        trend = _value(context.features, "1W", "trend")
        daily_trend = _value(context.features, "1d", "trend")
        structure = _value(context.features, "1W", "structure")
        if trend == "up" and daily_trend in {"up", "sideways"} and structure not in {"range", "lower-highs-lower-lows"}:
            return self._decision(context, side=SignalSide.LONG, score=80,
                reasons=(ReasonCode.ACCEPTED.value, "WEEKLY_TREND_CONFIRMED"),
                evidence={"weekly_trend": trend, "daily_trend": daily_trend, "weekly_structure": structure},
                stage=SignalStage.TRIGGER)
        if trend == "down" and daily_trend in {"down", "sideways"} and structure not in {"range", "higher-highs-higher-lows"}:
            return self._decision(context, side=SignalSide.SHORT, score=80,
                reasons=(ReasonCode.ACCEPTED.value, "WEEKLY_TREND_CONFIRMED"),
                evidence={"weekly_trend": trend, "daily_trend": daily_trend, "weekly_structure": structure},
                stage=SignalStage.TRIGGER)
        return self._no_trade(context, "weekly_trend_not_confirmed")


class WeeklyBreakoutPullback(BaseStrategy):
    def evaluate(self, context: StrategyContext) -> StrategyDecision:
        if not self.spec.enabled:
            return self._no_trade(context, "strategy_disabled")
        weekly = context.features.get("1W")
        close = _decimal(_value(context.features, "1W", "last_close"))
        high = _decimal(_value(context.features, "1W", "swing_high"))
        low = _decimal(_value(context.features, "1W", "swing_low"))
        daily_trend = _value(context.features, "1d", "trend")
        if close is None:
            return self._no_trade(context, "weekly_price_unavailable")
        breakout = high is not None and close >= high
        pullback = low is not None and close > low
        if daily_trend == "up" and (breakout or pullback):
            return self._decision(context, side=SignalSide.LONG, score=78,
                reasons=(ReasonCode.ACCEPTED.value, "WEEKLY_BREAKOUT_OR_PULLBACK"),
                evidence={"weekly_close": close, "weekly_swing_high": high, "weekly_swing_low": low, "daily_trend": daily_trend},
                stage=SignalStage.TRIGGER if breakout else SignalStage.SETUP)
        return self._no_trade(context, "weekly_breakout_pullback_not_confirmed")


class MTFTrendPullbackContinuation(BaseStrategy):
    def evaluate(self, context: StrategyContext) -> StrategyDecision:
        trend30 = _value(context.features, "30m", "trend")
        trend15 = _value(context.features, "15m", "trend")
        trend5 = _value(context.features, "5m", "trend")
        structure = _value(context.features, "15m", "structure")
        depth = _decimal(_value(context.features, "15m", "pullback_depth"))
        rvol = _decimal(_value(context.features, "5m", "rvol"))
        if trend30 == trend15 == trend5 == "up" and structure not in {"range", "lower-highs-lower-lows"} and depth is not None and Decimal("0.236") <= depth <= Decimal("0.618"):
            return self._decision(context, side=SignalSide.LONG, score=90 if rvol is not None and rvol >= Decimal("1.15") else 80,
                reasons=(ReasonCode.ACCEPTED.value, "MTF_TREND_ALIGNED", "FIBONACCI_PULLBACK"),
                evidence={"trend30": trend30, "trend15": trend15, "trend5": trend5, "structure15": structure, "pullback_depth": depth, "rvol5": rvol},
                stage=SignalStage.TRIGGER if rvol is not None and rvol >= Decimal("1.15") else SignalStage.SETUP)
        if trend30 == trend15 == trend5 == "down" and structure not in {"range", "higher-highs-higher-lows"} and depth is not None and Decimal("0.236") <= depth <= Decimal("0.618"):
            return self._decision(context, side=SignalSide.SHORT, score=90 if rvol is not None and rvol >= Decimal("1.15") else 80,
                reasons=(ReasonCode.ACCEPTED.value, "MTF_TREND_ALIGNED", "FIBONACCI_PULLBACK"),
                evidence={"trend30": trend30, "trend15": trend15, "trend5": trend5, "structure15": structure, "pullback_depth": depth, "rvol5": rvol},
                stage=SignalStage.TRIGGER if rvol is not None and rvol >= Decimal("1.15") else SignalStage.SETUP)
        return self._no_trade(context, "mtf_pullback_not_confirmed")


class VWAPRelativeStrengthContinuation(BaseStrategy):
    def evaluate(self, context: StrategyContext) -> StrategyDecision:
        trend = _value(context.features, "30m", "trend")
        close = _decimal(_value(context.features, "5m", "last_close"))
        vwap = _decimal(_value(context.features, "5m", "vwap"))
        rs = _decimal(getattr(context.analyzers.get("relative_strength"), "values", {}).get("excess_return")) if context.analyzers.get("relative_strength") else None
        rvol = _decimal(_value(context.features, "5m", "rvol"))
        if trend == "up" and close is not None and vwap is not None and close >= vwap and rs is not None and rs > 0 and rvol is not None and rvol >= Decimal("1.0"):
            return self._decision(context, side=SignalSide.LONG, score=88,
                reasons=(ReasonCode.ACCEPTED.value, "ABOVE_VWAP", "RELATIVE_STRENGTH_POSITIVE"),
                evidence={"trend30": trend, "close5": close, "vwap5": vwap, "excess_return": rs, "rvol5": rvol},
                stage=SignalStage.TRIGGER)
        if trend == "down" and close is not None and vwap is not None and close <= vwap and rs is not None and rs < 0 and rvol is not None and rvol >= Decimal("1.0"):
            return self._decision(context, side=SignalSide.SHORT, score=88,
                reasons=(ReasonCode.ACCEPTED.value, "BELOW_VWAP", "RELATIVE_STRENGTH_NEGATIVE"),
                evidence={"trend30": trend, "close5": close, "vwap5": vwap, "excess_return": rs, "rvol5": rvol},
                stage=SignalStage.TRIGGER)
        return self._no_trade(context, "vwap_relative_strength_not_confirmed")


class OpeningRangeBreakoutTrend(BaseStrategy):
    def evaluate(self, context: StrategyContext) -> StrategyDecision:
        trend = _value(context.features, "30m", "trend")
        close = _decimal(_value(context.features, "5m", "last_close"))
        swing_high = _decimal(_value(context.features, "5m", "swing_high"))
        swing_low = _decimal(_value(context.features, "5m", "swing_low"))
        rvol = _decimal(_value(context.features, "5m", "rvol"))
        if trend == "up" and close is not None and swing_high is not None and close >= swing_high and rvol is not None and rvol >= Decimal("1.5"):
            return self._decision(context, side=SignalSide.LONG, score=92,
                reasons=(ReasonCode.ACCEPTED.value, "OPENING_RANGE_BREAKOUT", "TREND_FILTER", "VOLUME_CONFIRMATION"),
                evidence={"trend30": trend, "close5": close, "range_high": swing_high, "rvol5": rvol},
                stage=SignalStage.TRIGGER)
        if trend == "down" and close is not None and swing_low is not None and close <= swing_low and rvol is not None and rvol >= Decimal("1.5"):
            return self._decision(context, side=SignalSide.SHORT, score=92,
                reasons=(ReasonCode.ACCEPTED.value, "OPENING_RANGE_BREAKOUT", "TREND_FILTER", "VOLUME_CONFIRMATION"),
                evidence={"trend30": trend, "close5": close, "range_low": swing_low, "rvol5": rvol},
                stage=SignalStage.TRIGGER)
        return self._no_trade(context, "opening_range_breakout_not_confirmed")


class StrategyManager:
    """Owns exactly one strategy instance and no shared mutable strategy state."""

    def __init__(self, strategy: Strategy) -> None:
        self.strategy = strategy
        self._last_dedupe_key: str | None = None

    @property
    def spec(self) -> StrategySpec:
        return self.strategy.spec

    def evaluate(self, context: StrategyContext) -> StrategyDecision:
        decision = self.strategy.evaluate(context)
        if decision.signal.dedupe_key == self._last_dedupe_key:
            return decision
        self._last_dedupe_key = decision.signal.dedupe_key
        return decision
