"""Immutable portfolio allocation and policy context."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class PortfolioId(StrEnum):
    LONG_TERM = "long_term"
    DAILY_TRADING = "daily_trading"


@dataclass(frozen=True, slots=True)
class PortfolioAllocation:
    portfolio_id: PortfolioId
    capital_budget: Decimal
    risk_budget: Decimal
    max_gross_exposure: Decimal
    max_net_exposure: Decimal

    def __post_init__(self) -> None:
        values = (self.capital_budget, self.risk_budget, self.max_gross_exposure, self.max_net_exposure)
        if any(value < 0 for value in values):
            raise ValueError("portfolio allocation values cannot be negative")


@dataclass(frozen=True, slots=True)
class PortfolioContext:
    allocation: PortfolioAllocation
    holding_period: str
    primary_timeframe: str
    confirmation_timeframe: str | None
    session_policy: str
    liquidation_policy: str

    @property
    def portfolio_id(self) -> PortfolioId:
        return self.allocation.portfolio_id


LONG_TERM_CONTEXT = PortfolioContext(
    allocation=PortfolioAllocation(
        portfolio_id=PortfolioId.LONG_TERM,
        capital_budget=Decimal("0"),
        risk_budget=Decimal("0"),
        max_gross_exposure=Decimal("0"),
        max_net_exposure=Decimal("0"),
    ),
    holding_period="multi_week_to_multi_month",
    primary_timeframe="1W",
    confirmation_timeframe="1D",
    session_policy="hold_through_sessions",
    liquidation_policy="weekly_trend_or_thesis_failure_only",
)

DAILY_TRADING_CONTEXT = PortfolioContext(
    allocation=PortfolioAllocation(
        portfolio_id=PortfolioId.DAILY_TRADING,
        capital_budget=Decimal("0"),
        risk_budget=Decimal("0"),
        max_gross_exposure=Decimal("0"),
        max_net_exposure=Decimal("0"),
    ),
    holding_period="intraday",
    primary_timeframe="30m/15m/5m",
    confirmation_timeframe="5m",
    session_policy="flatten_by_session_policy",
    liquidation_policy="intraday_trade_exit",
)
