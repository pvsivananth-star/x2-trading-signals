from datetime import UTC, datetime
from decimal import Decimal
from app.application.trades import TradeManager
from app.application.portfolio_controller import PortfolioController,PortfolioLimits,PortfolioSnapshot
from app.application.risk_manager import RiskManager,RiskLimits,RiskSnapshot
from app.application.circuit_breakers import CircuitBreaker
from app.domain.risk.model import KillSwitchState
from app.domain.signals import *
from app.application.signals import Signal,SignalSide,SignalStage
from app.application.strategy_manager import StrategyDecision

def decision():
 t=datetime(2026,1,1,tzinfo=UTC); s=Signal.build(symbol="X",timestamp=t,causal_cutoff=t,stage=SignalStage.TRIGGER,side=SignalSide.LONG,portfolio_id="daily_trading",strategy_id="s",strategy_version="1",config_version="c",score=90,reason_codes=("ACCEPTED",),evidence={"x":1},provenance={})
 return StrategyDecision("s","1","daily_trading","X",t,SignalStage.TRIGGER,SignalSide.LONG,90,("ACCEPTED",),(),s)
def test_trade_intent_only_from_trigger():
 i=TradeManager().create_intent(decision(),quantity=Decimal("10"),now=datetime(2026,1,1,tzinfo=UTC))
 assert i.side=="BUY" and i.status.value=="CREATED"
def test_portfolio_reservation_gate():
 p=PortfolioController(PortfolioLimits(10000,500,8000,8000,300))
 d=p.evaluate(TradeManager().create_intent(decision(),quantity=10,now=datetime(2026,1,1,tzinfo=UTC),limit_price=100),PortfolioSnapshot(10000,0,0,0,0),risk_amount=100,expected_notional=1000,reservation_id="r1")
 assert d.approved and d.reservation_id=="r1"
def test_risk_kill_switch_rejects():
 r=RiskManager(RiskLimits(100,1000,1000,3,5000))
 i=TradeManager().create_intent(decision(),quantity=1,now=datetime(2026,1,1,tzinfo=UTC),limit_price=100)
 d=r.evaluate(i,risk_amount=10,snapshot=RiskSnapshot(kill_switch=KillSwitchState.ACTIVE),now=datetime(2026,1,1,tzinfo=UTC),decision_id="d")
 assert d.status.value=="RISK_OFF"
def test_breaker_release_fails_closed():
 b=CircuitBreaker(); b.activate(reason="test",operator="x")
 try: b.release(operator="x"); assert False
 except RuntimeError: pass
