"""Deterministic event replay clock and run orchestration."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.backtest.bars import Bar, Trade, rollup
from app.features import feature_snapshot
from app.quality import QualityFinding


@dataclass(frozen=True, slots=True)
class ReplayProvenance:
    run_id: str
    config_hash: str
    code_hash: str
    data_provenance: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ReplayResult:
    provenance: ReplayProvenance
    bars: tuple[Bar, ...]
    equity_points: tuple[tuple[datetime, Decimal], ...]
    fills: tuple[object, ...]
    metrics: Mapping[str, Decimal]
    quality_summary: Mapping[str, int]


class ReplayClock:
    def __init__(self, events: Iterable[object]) -> None:
        self.events = tuple(sorted(events, key=lambda event: event.event_time))
        self.index = 0

    @property
    def now(self) -> datetime | None:
        return (
            getattr(self.events[self.index], "event_time", None)
            if self.index < len(self.events)
            else None
        )

    def next(self) -> object | None:
        if self.index >= len(self.events):
            return None
        event = self.events[self.index]
        self.index += 1
        return event


def chronological_split(
    values: Sequence[object], train: float, validation: float
) -> tuple[list[object], list[object], list[object]]:
    if train <= 0 or validation <= 0 or train + validation >= 1:
        raise ValueError("invalid train/validation fractions")
    ordered = list(values)
    train_end = int(len(ordered) * train)
    validation_end = int(len(ordered) * (train + validation))
    return ordered[:train_end], ordered[train_end:validation_end], ordered[validation_end:]


def walk_forward_splits(
    values: Sequence[object], train_size: int, test_size: int
) -> list[tuple[list[object], list[object]]]:
    if train_size <= 0 or test_size <= 0:
        raise ValueError("positive walk-forward window sizes are required")
    splits: list[tuple[list[object], list[object]]] = []
    for start in range(0, len(values) - train_size - test_size + 1, test_size):
        train = list(values[start : start + train_size])
        test = list(values[start + train_size : start + train_size + test_size])
        splits.append((train, test))
    return splits


def calculate_metrics(
    equity_points: Sequence[tuple[datetime, Decimal]],
    returns: Sequence[Decimal] | None = None,
    trade_pnl: Sequence[Decimal] = (),
    starting_equity: Decimal = Decimal(1),
) -> Mapping[str, Decimal]:
    pnl = list(trade_pnl)
    wins = [value for value in pnl if value > 0]
    losses = [value for value in pnl if value < 0]
    gross_profit = sum(wins, Decimal(0))
    gross_loss = abs(sum(losses, Decimal(0)))
    profit_factor = gross_profit / gross_loss if gross_loss else gross_profit

    peak = starting_equity
    max_drawdown = Decimal(0)
    for _, equity in equity_points:
        peak = max(peak, equity)
        if peak:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)
    win_rate = Decimal(len(wins)) / Decimal(len(pnl)) if pnl else Decimal(0)
    expectancy = sum(pnl, Decimal(0)) / Decimal(len(pnl)) if pnl else Decimal(0)

    positive_streak = current_positive = 0
    negative_streak = current_negative = 0
    for value in pnl:
        if value > 0:
            current_positive += 1
            positive_streak = max(positive_streak, current_positive)
            current_negative = 0
        else:
            current_negative += 1 if value < 0 else 0
            negative_streak = max(negative_streak, current_negative)
            if value >= 0:
                current_positive = 0

    return {
        "profit_factor": profit_factor,
        "max_drawdown": max_drawdown,
        "win_rate": win_rate,
        "expectancy": expectancy,
        "max_win_streak": Decimal(positive_streak),
        "max_loss_streak": Decimal(negative_streak),
        "exposure": Decimal(len([p for p in equity_points if p[1] != starting_equity]))
        / Decimal(len(equity_points) or 1),
        "turnover": Decimal(len(pnl)),
    }


class ReplayRun:
    def __init__(
        self,
        *,
        interval_seconds: int = 300,
        gap_policy: str = "annotate",
        quality_threshold: int = 0,
    ) -> None:
        if gap_policy not in {"fail", "annotate"}:
            raise ValueError("gap_policy must be fail or annotate")
        self.interval_seconds = interval_seconds
        self.gap_policy = gap_policy
        self.quality_threshold = quality_threshold

    def run(
        self,
        events: Sequence[object],
        config: Mapping[str, object],
        data_provenance: Mapping[str, str] | None = None,
        quality_findings: Sequence[QualityFinding] = (),
    ) -> ReplayResult:
        normalized = sorted(events, key=lambda event: event.event_time)
        trades = [Trade(event.event_time, event.price, event.quantity) for event in normalized]
        bars = tuple(rollup(trades, self.interval_seconds))
        for bar in bars:
            feature_snapshot(list(bars), bar.bar_end)
        equity_points = tuple((bar.bar_end, bar.close) for bar in bars)
        finding_count = len(tuple(quality_findings))
        if finding_count > self.quality_threshold and self.gap_policy == "fail":
            raise RuntimeError(f"quality threshold exceeded: {finding_count} findings")
        config_hash = hashlib.sha256(
            json.dumps(config, sort_keys=True, default=str).encode()
        ).hexdigest()
        code_payload = json.dumps(
            {
                "module": "app.backtest.replay",
                "features": True,
                "interval_seconds": self.interval_seconds,
            },
            sort_keys=True,
        ).encode()
        code_hash = hashlib.sha256(code_payload).hexdigest()
        run_id = hashlib.sha256(
            (config_hash + code_hash + str(tuple(data_provenance or {}.items()))).encode()
        ).hexdigest()[:32]
        provenance = ReplayProvenance(run_id, config_hash, code_hash, dict(data_provenance or {}))
        metrics = calculate_metrics(equity_points)
        return ReplayResult(
            provenance,
            bars,
            equity_points,
            (),
            metrics,
            {"finding_count": finding_count, "gap_policy": 1 if self.gap_policy == "fail" else 0},
        )
