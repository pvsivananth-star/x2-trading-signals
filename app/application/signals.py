"""Phase-3 signal management: causal, immutable SETUP/TRIGGER signals.

Signals are candidates only. They never authorize trades or broker orders.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Mapping, Sequence

from app.application.analyzers.core import AnalyzerResult
from app.domain.reason_codes import ReasonCode


class SignalStage(StrEnum):
    SETUP = "SETUP"
    TRIGGER = "TRIGGER"
    NO_TRADE = "NO_TRADE"


class SignalSide(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("signal timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _canonical(values: Mapping[str, object]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(k), json.dumps(v, sort_keys=True, default=str)) for k, v in values.items()))


@dataclass(frozen=True, slots=True)
class Signal:
    signal_id: str
    dedupe_key: str
    symbol: str
    timestamp: datetime
    causal_cutoff: datetime
    stage: SignalStage
    side: SignalSide
    portfolio_id: str
    strategy_id: str
    strategy_version: str
    config_version: str
    score: int
    reason_codes: tuple[str, ...]
    evidence: tuple[tuple[str, str], ...]
    provenance: tuple[tuple[str, str], ...]
    parent_signal_id: str | None = None

    def __post_init__(self) -> None:
        if not self.symbol or not self.strategy_id or not self.strategy_version:
            raise ValueError("symbol, strategy_id and strategy_version are required")
        if self.score < 0 or self.score > 100:
            raise ValueError("signal score must be between 0 and 100")
        if self.stage is SignalStage.TRIGGER and self.side is SignalSide.FLAT:
            raise ValueError("TRIGGER signals require a side")
        object.__setattr__(self, "timestamp", _utc(self.timestamp))
        object.__setattr__(self, "causal_cutoff", _utc(self.causal_cutoff))
        if self.causal_cutoff > self.timestamp:
            raise ValueError("causal cutoff cannot be later than signal timestamp")
        if not self.reason_codes:
            raise ValueError("signals require reason codes")

    @classmethod
    def build(
        cls,
        *,
        symbol: str,
        timestamp: datetime,
        causal_cutoff: datetime,
        stage: SignalStage,
        side: SignalSide,
        portfolio_id: str,
        strategy_id: str,
        strategy_version: str,
        config_version: str,
        score: int,
        reason_codes: Sequence[str],
        evidence: Mapping[str, object],
        provenance: Mapping[str, str],
        parent_signal_id: str | None = None,
    ) -> "Signal":
        ts = _utc(timestamp)
        cutoff = _utc(causal_cutoff)
        ev = _canonical(evidence)
        prov = tuple(sorted((str(k), str(v)) for k, v in provenance.items()))
        reasons = tuple(dict.fromkeys(str(x) for x in reason_codes))
        payload = {
            "symbol": symbol, "timestamp": ts.isoformat(), "causal_cutoff": cutoff.isoformat(),
            "stage": stage.value, "side": side.value, "portfolio_id": portfolio_id,
            "strategy_id": strategy_id, "strategy_version": strategy_version,
            "config_version": config_version, "score": score, "reason_codes": reasons,
            "evidence": ev, "provenance": prov, "parent_signal_id": parent_signal_id,
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
        dedupe = f"{symbol}:{strategy_id}:{strategy_version}:{ts.isoformat()}:{stage.value}:{side.value}:{digest[:20]}"
        return cls(
            signal_id=digest, dedupe_key=dedupe, symbol=symbol, timestamp=ts,
            causal_cutoff=cutoff, stage=stage, side=side, portfolio_id=portfolio_id,
            strategy_id=strategy_id, strategy_version=strategy_version,
            config_version=config_version, score=score, reason_codes=reasons,
            evidence=ev, provenance=prov, parent_signal_id=parent_signal_id,
        )


class SignalManager:
    """Converts analyzer facts into candidate signals; never creates orders."""

    def evaluate(
        self,
        *,
        symbol: str,
        timestamp: datetime,
        causal_cutoff: datetime,
        portfolio_id: str,
        strategy_id: str,
        strategy_version: str,
        config_version: str,
        analyzers: Mapping[str, AnalyzerResult],
        side: SignalSide,
        provenance: Mapping[str, str] | None = None,
        setup_min_score: int = 50,
        trigger_min_score: int = 75,
    ) -> tuple[Signal, ...]:
        provenance = provenance or {}
        score = 0
        evidence: dict[str, object] = {}
        reasons: list[str] = []
        for name, result in sorted(analyzers.items()):
            evidence[name] = {"passed": result.passed, "values": dict(result.values), "reasons": result.reasons}
            if result.passed:
                score += 100 // max(len(analyzers), 1)
            else:
                reasons.extend(result.reasons)
        score = min(score, 100)
        if not analyzers:
            reasons.append(ReasonCode.DATA_UNAVAILABLE.value)
        if score < setup_min_score:
            return (Signal.build(
                symbol=symbol, timestamp=timestamp, causal_cutoff=causal_cutoff,
                stage=SignalStage.NO_TRADE, side=SignalSide.FLAT,
                portfolio_id=portfolio_id, strategy_id=strategy_id, strategy_version=strategy_version,
                config_version=config_version, score=score,
                reason_codes=(ReasonCode.NO_TRADE.value, *reasons),
                evidence=evidence, provenance=provenance,
            ),)
        stage = SignalStage.TRIGGER if score >= trigger_min_score else SignalStage.SETUP
        if stage is SignalStage.TRIGGER and side is SignalSide.FLAT:
            stage = SignalStage.SETUP
            reasons.append(ReasonCode.NO_TRADE.value)
        reasons = [ReasonCode.ACCEPTED.value, *reasons] if stage is not SignalStage.NO_TRADE else reasons
        return (Signal.build(
            symbol=symbol, timestamp=timestamp, causal_cutoff=causal_cutoff,
            stage=stage, side=side, portfolio_id=portfolio_id,
            strategy_id=strategy_id, strategy_version=strategy_version,
            config_version=config_version, score=score,
            reason_codes=tuple(reasons), evidence=evidence, provenance=provenance,
        ),)
