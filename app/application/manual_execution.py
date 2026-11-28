"""Explicitly authorized manual execution boundary.

Manual execution never bypasses portfolio/risk controls and is disabled outside
paper/demo modes. Live broker routing remains impossible through this boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.application.portfolio_controller import PortfolioController, PortfolioSnapshot
from app.application.risk_manager import RiskManager, RiskSnapshot
from app.application.trades import TradeManager
from app.domain.runtime import RuntimeMode
from app.domain.risk.model import RiskDecisionStatus


@dataclass(frozen=True, slots=True)
class Operator:
    operator_id: str
    roles: frozenset[str]

    def can_execute(self) -> bool:
        return "manual_trader" in self.roles and "risk_approved" in self.roles


@dataclass(frozen=True, slots=True)
class ManualExecutionRequest:
    symbol: str
    portfolio_id: str
    strategy_id: str
    side: str
    quantity: Decimal
    reason: str


class ManualExecutionGate:
    def __init__(
        self,
        portfolio: PortfolioController,
        risk: RiskManager,
        trade_manager: TradeManager,
        *,
        runtime_mode: RuntimeMode,
    ) -> None:
        self.portfolio = portfolio
        self.risk = risk
        self.trade_manager = trade_manager
        self.runtime_mode = runtime_mode

    def authorize(
        self,
        operator: Operator,
        request: ManualExecutionRequest,
        *,
        timestamp: datetime,
        snapshot: PortfolioSnapshot,
        risk_snapshot: RiskSnapshot,
        reference_price: Decimal,
    ) -> tuple[bool, tuple[str, ...]]:
        if self.runtime_mode not in {RuntimeMode.PAPER, RuntimeMode.DEMO}:
            return False, ("MANUAL_EXECUTION_MODE_NOT_ALLOWED",)
        if not operator.can_execute():
            return False, ("OPERATOR_NOT_AUTHORIZED",)
        if request.quantity <= 0 or reference_price <= 0:
            return False, ("INVALID_MANUAL_ORDER",)
        if request.side not in {"BUY", "SELL"}:
            return False, ("INVALID_MANUAL_SIDE",)
        if not request.reason.strip():
            return False, ("MANUAL_REASON_REQUIRED",)
        if not risk_snapshot.reconciled or risk_snapshot.kill_switch.value == "active":
            return False, ("RISK_CONTROL_BLOCKED",)

        expected = request.quantity * reference_price
        # Manual execution is still a portfolio/risk decision. No broker is reachable here.
        if abs(request.quantity) <= 0:
            return False, ("INVALID_MANUAL_ORDER",)
        return self._evaluate(operator, request, timestamp, snapshot, risk_snapshot, expected)

    def _evaluate(self, operator, request, timestamp, snapshot, risk_snapshot, expected):
        # The concrete TradeIntent construction remains owned by TradeManager; this
        # boundary intentionally returns an authorization decision rather than placing an order.
        portfolio_ok = snapshot.available_capital() >= expected
        if not portfolio_ok:
            return False, ("INSUFFICIENT_AVAILABLE_CAPITAL",)
        return True, ("MANUAL_AUTHORIZED",)
