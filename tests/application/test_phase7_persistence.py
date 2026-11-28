from datetime import UTC, datetime
from decimal import Decimal

from app.application.manual_execution import ManualExecutionGate, ManualExecutionRequest, Operator
from app.application.persistence import InMemoryAuditEventStore, InMemoryBacktestArtifactStore, InMemoryOperationalStateStore
from app.application.read_models import API_VERSION, Page
from app.domain.event_envelope import EventEnvelope
from app.domain.runtime import RuntimeMode


def test_operational_state_and_backtest_artifacts_are_isolated():
    state = InMemoryOperationalStateStore()
    artifacts = InMemoryBacktestArtifactStore()
    state.put("kill_switch", True)
    artifacts.save("run-1", {"pnl": Decimal("10")})
    assert state.get("kill_switch") is True
    assert artifacts.get("run-1")["pnl"] == Decimal("10")
    assert artifacts.list() == ("run-1",)


def test_audit_store_is_idempotent():
    store = InMemoryAuditEventStore()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    event = EventEnvelope(
        event_type="test",
        version=1,
        occurred_at=now,
        observed_at=now,
        causal_cutoff=now,
        correlation_id="c1",
        causation_id=None,
        aggregate_id="a1",
        producer="test",
        producer_version="1",
        provenance={"source": "test"},
        payload={"x": 1},
    )
    store.append(event)
    store.append(event)
    assert len(store.by_correlation("c1")) == 1
    assert store.by_aggregate("a1")[0].event_id == event.event_id


def test_api_page_has_stable_version():
    page = Page(({"id": "1"},), 1, 50, 0)
    assert page.to_dict()["api_version"] == API_VERSION


def test_manual_execution_requires_explicit_roles_and_paper_mode():
    from app.application.portfolio_controller import PortfolioController, PortfolioLimits
    from app.application.risk_manager import RiskLimits, RiskManager
    from app.application.trades import TradeManager
    from app.domain.risk.model import KillSwitchState

    portfolio = PortfolioController(PortfolioLimits(Decimal("1000"), Decimal("100"), Decimal("1000"), Decimal("1000"), Decimal("100")))
    risk = RiskManager(RiskLimits(Decimal("100"), Decimal("100"), Decimal("0.2"), 3, Decimal("1000")))
    gate = ManualExecutionGate(portfolio, risk, TradeManager(), runtime_mode=RuntimeMode.PAPER)
    req = ManualExecutionRequest("AAPL", "daily_trading", "manual", "BUY", Decimal("1"), "operator test")
    snapshot = __import__("app.application.portfolio_controller", fromlist=["PortfolioSnapshot"]).PortfolioSnapshot(Decimal("1000"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"))
    risk_snapshot = __import__("app.application.risk_manager", fromlist=["RiskSnapshot"]).RiskSnapshot(kill_switch=KillSwitchState.INACTIVE)
    denied = gate.authorize(Operator("op", frozenset()), req, timestamp=datetime.now(UTC), snapshot=snapshot, risk_snapshot=risk_snapshot, reference_price=Decimal("10"))
    assert denied[0] is False
    allowed = gate.authorize(Operator("op", frozenset({"manual_trader", "risk_approved"})), req, timestamp=datetime.now(UTC), snapshot=snapshot, risk_snapshot=risk_snapshot, reference_price=Decimal("10"))
    assert allowed[0] is True
