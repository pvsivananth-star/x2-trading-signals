"""Central portfolio gate: allocation, reservations and account-wide exposure."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.domain.reason_codes import ReasonCode
from app.domain.trades.model import TradeIntent


@dataclass(frozen=True, slots=True)
class PortfolioLimits:
    capital_budget: Decimal
    risk_budget: Decimal
    max_gross_exposure: Decimal
    max_net_exposure: Decimal
    max_strategy_risk: Decimal

    def __post_init__(self) -> None:
        values = (
            self.capital_budget,
            self.risk_budget,
            self.max_gross_exposure,
            self.max_net_exposure,
            self.max_strategy_risk,
        )
        if any(value < 0 for value in values):
            raise ValueError("limits cannot be negative")


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    cash: Decimal
    reserved_capital: Decimal
    consumed_risk: Decimal
    gross_exposure: Decimal
    net_exposure: Decimal

    def available_capital(self) -> Decimal:
        return max(Decimal(0), self.cash - self.reserved_capital)


@dataclass(frozen=True, slots=True)
class PortfolioDecision:
    approved: bool
    reason_codes: tuple[str, ...]
    reservation_id: str | None = None
    reserved_capital: Decimal = Decimal(0)
    reserved_risk: Decimal = Decimal(0)


class PortfolioController:
    def __init__(
        self,
        limits: PortfolioLimits,
        *,
        account_max_gross: Decimal | None = None,
        account_max_net: Decimal | None = None,
    ) -> None:
        self.limits = limits
        self.account_max_gross = (
            limits.max_gross_exposure if account_max_gross is None else account_max_gross
        )
        self.account_max_net = (
            limits.max_net_exposure if account_max_net is None else account_max_net
        )
        self._reservations: dict[str, tuple[TradeIntent, Decimal, Decimal]] = {}

    def evaluate(
        self,
        intent: TradeIntent,
        snapshot: PortfolioSnapshot,
        *,
        risk_amount: Decimal,
        expected_notional: Decimal,
        reservation_id: str,
    ) -> PortfolioDecision:
        reasons: list[str] = []
        if not intent.portfolio_id or not intent.strategy_id:
            reasons.append(ReasonCode.PORTFOLIO_REJECTED.value)
        if risk_amount <= 0 or expected_notional <= 0:
            reasons.append(ReasonCode.PORTFOLIO_REJECTED.value)
        if snapshot.available_capital() < expected_notional:
            reasons.append("INSUFFICIENT_AVAILABLE_CAPITAL")

        reservations = tuple(self._reservations.values())
        reserved_risk = sum(
            item[2] for item in reservations if item[0].portfolio_id == intent.portfolio_id
        )
        if snapshot.consumed_risk + reserved_risk + risk_amount > self.limits.risk_budget:
            reasons.append("PORTFOLIO_RISK_BUDGET_EXCEEDED")

        reserved_gross = sum(
            item[1] for item in reservations if item[0].portfolio_id == intent.portfolio_id
        )
        projected_gross = snapshot.gross_exposure + reserved_gross + expected_notional
        if projected_gross > self.limits.max_gross_exposure:
            reasons.append("PORTFOLIO_GROSS_EXPOSURE_EXCEEDED")

        signed_notional = expected_notional if intent.side == "BUY" else -expected_notional
        projected_net = snapshot.net_exposure + signed_notional
        if abs(projected_net) > self.limits.max_net_exposure:
            reasons.append("PORTFOLIO_NET_EXPOSURE_EXCEEDED")
        if projected_gross > self.account_max_gross:
            reasons.append("ACCOUNT_GROSS_EXPOSURE_EXCEEDED")
        if abs(projected_net) > self.account_max_net:
            reasons.append("ACCOUNT_NET_EXPOSURE_EXCEEDED")

        strategy_reserved_risk = sum(
            item[2]
            for item in reservations
            if item[0].portfolio_id == intent.portfolio_id
            and item[0].strategy_id == intent.strategy_id
        )
        if strategy_reserved_risk + risk_amount > self.limits.max_strategy_risk:
            reasons.append("STRATEGY_RISK_BUDGET_EXCEEDED")

        if reasons:
            return PortfolioDecision(False, tuple(dict.fromkeys(reasons)))

        self._reservations[reservation_id] = (intent, expected_notional, risk_amount)
        return PortfolioDecision(
            True,
            (ReasonCode.ACCEPTED.value,),
            reservation_id,
            expected_notional,
            risk_amount,
        )

    def release(self, reservation_id: str) -> None:
        self._reservations.pop(reservation_id, None)
