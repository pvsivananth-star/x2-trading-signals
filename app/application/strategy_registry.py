"""Strategy registry and portfolio-scoped orchestration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from app.application.strategy_manager import (
    BaseStrategy, MTFTrendPullbackContinuation, OpeningRangeBreakoutTrend,
    Strategy, StrategyContext, StrategyDecision, StrategyManager, StrategySpec,
    WeeklyBreakoutPullback, WeeklyTrendFollowing, VWAPRelativeStrengthContinuation,
)


@dataclass(frozen=True, slots=True)
class StrategyAssignment:
    strategy_id: str
    portfolio_id: str
    version: str
    enabled: bool
    config_version: str


class StrategyRegistry:
    def __init__(self) -> None:
        self._managers: dict[str, StrategyManager] = {}

    def register(self, manager: StrategyManager) -> None:
        sid = manager.spec.strategy_id
        if sid in self._managers:
            raise ValueError(f"strategy already registered: {sid}")
        self._managers[sid] = manager

    def get(self, strategy_id: str) -> StrategyManager:
        try:
            return self._managers[strategy_id]
        except KeyError as exc:
            raise KeyError(f"unknown strategy: {strategy_id}") from exc

    def for_portfolio(self, portfolio_id: str) -> tuple[StrategyManager, ...]:
        return tuple(m for m in self._managers.values() if m.spec.portfolio_id == portfolio_id and m.spec.enabled)

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._managers))


class PortfolioStrategyOrchestrator:
    """Runs enabled strategy managers independently; it does not approve trades."""

    def __init__(self, registry: StrategyRegistry) -> None:
        self.registry = registry

    def evaluate(self, context: StrategyContext, *, portfolio_id: str | None = None) -> tuple[StrategyDecision, ...]:
        managers = self.registry.for_portfolio(portfolio_id or context.features.get("portfolio_id", ""))
        return tuple(manager.evaluate(context) for manager in managers)


def default_registry(*, config_version: str = "phase3") -> StrategyRegistry:
    registry = StrategyRegistry()
    specs = (
        StrategySpec("weekly_trend_following_v1", "1.0.0", "long_term", True, config_version),
        StrategySpec("weekly_breakout_pullback_v1", "1.0.0", "long_term", True, config_version),
        StrategySpec("mtf_trend_pullback_v1", "1.0.0", "daily_trading", True, config_version),
        StrategySpec("vwap_relative_strength_v1", "1.0.0", "daily_trading", True, config_version),
        StrategySpec("opening_range_breakout_v1", "1.0.0", "daily_trading", True, config_version),
    )
    classes = (
        WeeklyTrendFollowing, WeeklyBreakoutPullback,
        MTFTrendPullbackContinuation, VWAPRelativeStrengthContinuation, OpeningRangeBreakoutTrend,
    )
    for spec, cls in zip(specs, classes):
        registry.register(StrategyManager(cls(spec)))
    return registry
