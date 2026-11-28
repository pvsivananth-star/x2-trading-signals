"""Explicitly authorized manual execution boundary."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.application.portfolio_controller import PortfolioController, PortfolioSnapshot
from app.application.risk_manager import RiskManager, RiskSnapshot
from app.application.trades import TradeManager
from app.domain.runtime import RuntimeMode
from app.domain.risk.model import RiskDecisionStatus
from app.domain.trades.model import TradeIntent


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
    """Authorizes a manual intent; it never submits an order itself."""

    def __init__(self, portfolio: PortfolioController, risk: RiskManager,
                 trade_manager: TradeManager, *, runtime_mode: RuntimeMode) -> None:
        self.portfolio = portfolio
        self.risk = risk
        self.trade_manager = trade_manager
        self.runtime_mode = runtime_mode

    def authorize(self, operator: Operator, request: ManualExecutionRequest, *,
                  timestamp: datetime, snapshot: PortfolioSnapshot,
                  risk_snapshot: RiskSnapshot, reference_price: Decimal) -> tuple[bool, tuple[str, ...]]:
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
        intent_id = hashlib.sha256(
            f"manual:{request.symbol}:{request.side}:{request.quantity}:{timestamp.isoformat()}".encode()
        ).hexdigest()[:32]
        intent = TradeIntent(
            intent_id=intent_id, symbol=request.symbol, side=request.side,
            quantity=request.quantity, portfolio_id=request.portfolio_id,
            strategy_id=request.strategy_id, strategy_version="manual-v1",
            signal_id=f"manual:{intent_id}", created_at=timestamp,
            limit_price=reference_price, reason_codes=("MANUAL_OPERATOR_AUTHORIZED",),
        )
        risk_amount = expected * Decimal("0.01")
        portfolio_decision = self.portfolio.evaluate(
            intent, snapshot, risk_amount=risk_amount,
            expected_notional=expected, reservation_id=intent.intent_id,
        )
        if not portfolio_decision.approved:
            return False, portfolio_decision.reason_codes
        risk_decision = self.risk.evaluate(
            intent, risk_amount=risk_amount, snapshot=risk_snapshot,
            now=timestamp, decision_id=f"manual-risk:{intent.intent_id}",
        )
        self.portfolio.release(intent.intent_id)
        if risk_decision.status is not RiskDecisionStatus.APPROVED:
            return False, risk_decision.reason_codes
        return True, ("MANUAL_AUTHORIZED",)
