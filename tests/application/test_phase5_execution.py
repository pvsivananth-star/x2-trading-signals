from datetime import datetime, timezone
from decimal import Decimal

from app.application.execution import ExecutionManager, InMemoryExecutionAdapter
from app.domain.orders.model import OrderReplacement, OrderStatus
from app.domain.risk.model import RiskDecisionStatus
from app.domain.runtime import RuntimeMode
from app.domain.trades.model import TradeIntent


def intent():
    return TradeIntent(
        intent_id="TI-1", symbol="ABC", side="BUY", quantity=Decimal("10"),
        portfolio_id="daily_trading", strategy_id="s1", strategy_version="v1",
        signal_id="sig-1", created_at=datetime.now(timezone.utc), reason_codes=("ACCEPTED",),
    )


def test_execution_requires_portfolio_and_risk_approval():
    m = ExecutionManager(InMemoryExecutionAdapter(), runtime_mode=RuntimeMode.DEMO)
    result = m.submit(intent(), portfolio_approved=False, risk_status=RiskDecisionStatus.REJECTED, timestamp=datetime.now(timezone.utc))
    assert not result.accepted
    assert result.reason == "PORTFOLIO_REJECTED"


def test_idempotent_submit_does_not_duplicate_broker_call():
    adapter = InMemoryExecutionAdapter()
    m = ExecutionManager(adapter, runtime_mode=RuntimeMode.DEMO)
    t = intent()
    now = datetime.now(timezone.utc)
    first = m.submit(t, portfolio_approved=True, risk_status=RiskDecisionStatus.APPROVED, timestamp=now)
    second = m.submit(t, portfolio_approved=True, risk_status=RiskDecisionStatus.APPROVED, timestamp=now)
    assert first.accepted and second.accepted
    assert adapter.calls.count("TI-1") == 1


def test_partial_fill_and_cancel_replace_state_machine():
    adapter = InMemoryExecutionAdapter()
    m = ExecutionManager(adapter, runtime_mode=RuntimeMode.DEMO)
    t = intent()
    now = datetime.now(timezone.utc)
    result = m.submit(t, portfolio_approved=True, risk_status=RiskDecisionStatus.APPROVED, timestamp=now)
    order = result.order
    partial = m.apply_update(type("U", (), {
        "order_id": order.order_id, "status": OrderStatus.WORKING, "timestamp": now,
        "broker_order_id": "B1", "filled_quantity": Decimal("4"),
        "average_fill_price": Decimal("100"), "reject_reason": None
    })())
    assert partial.status is OrderStatus.PARTIALLY_FILLED
    assert partial.remaining_quantity == Decimal("6")
    assert m.replace(order.order_id, OrderReplacement(quantity=Decimal("10"), limit_price=Decimal("101")), timestamp=now).accepted
    assert m.cancel(order.order_id, timestamp=now).accepted


def test_broker_exception_fails_closed_and_reconciliation_is_required():
    class Broken(InMemoryExecutionAdapter):
        def submit(self, order, idempotency_key):
            raise RuntimeError("disconnect")
    m = ExecutionManager(Broken(), runtime_mode=RuntimeMode.DEMO)
    result = m.submit(intent(), portfolio_approved=True, risk_status=RiskDecisionStatus.APPROVED, timestamp=datetime.now(timezone.utc))
    assert not result.accepted
    assert result.order.status is OrderStatus.UNKNOWN
    assert not m.reconciled
