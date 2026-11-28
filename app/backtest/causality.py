"""Causal guards used by backtests and replay.

The guard makes the information boundary explicit: decisions may consume only data whose
bar_end is at or before the decision cutoff, and an order generated from a closed bar may
not execute on that same bar.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Iterable, Sequence

from app.backtest.bars import Bar


@dataclass(frozen=True, slots=True)
class CausalViolation:
    code: str
    message: str
    timestamp: datetime | None = None


def causal_bars(bars: Iterable[Bar], cutoff: datetime) -> tuple[Bar, ...]:
    """Return only bars that were closed by the causal cutoff."""
    cutoff = cutoff.astimezone(UTC)
    return tuple(
        bar for bar in bars
        if bar.bar_end.astimezone(UTC) <= cutoff
    )


def validate_causal_window(
    bars: Sequence[Bar],
    cutoff: datetime,
) -> tuple[CausalViolation, ...]:
    """Report any bar that would leak information beyond the cutoff."""
    cutoff = cutoff.astimezone(UTC)
    return tuple(
        CausalViolation(
            "FUTURE_BAR",
            "bar_end is after the decision causal cutoff",
            bar.bar_end,
        )
        for bar in bars
        if bar.bar_end.astimezone(UTC) > cutoff
    )


def validate_next_bar_execution(
    decision_bar: Bar,
    execution_bar: Bar,
) -> tuple[CausalViolation, ...]:
    """Require execution to occur strictly after the bar that generated the decision."""
    if execution_bar.bar_start.astimezone(UTC) < decision_bar.bar_end.astimezone(UTC):
        return (
            CausalViolation(
                "SAME_BAR_EXECUTION",
                "execution occurs before the decision bar closed",
                execution_bar.bar_start,
            ),
        )
    return ()


def assert_causal_window(bars: Sequence[Bar], cutoff: datetime) -> None:
    violations = validate_causal_window(bars, cutoff)
    if violations:
        raise ValueError(violations[0].message)


def assert_next_bar_execution(decision_bar: Bar, execution_bar: Bar) -> None:
    violations = validate_next_bar_execution(decision_bar, execution_bar)
    if violations:
        raise ValueError(violations[0].message)
