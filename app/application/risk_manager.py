"""Independent risk gate and circuit breakers."""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from app.domain.reason_codes import ReasonCode
from app.domain.risk.model import KillSwitchState, RiskDecision, RiskDecisionStatus

@dataclass(frozen=True, slots=True)
class RiskLimits:
    max_trade_risk: Decimal
    max_daily_loss: Decimal
    max_drawdown: Decimal
    max_consecutive_losses: int
    max_position_notional: Decimal
    def __post_init__(self):
        if any(x<0 for x in (self.max_trade_risk,self.max_daily_loss,self.max_drawdown,self.max_position_notional)) or self.max_consecutive_losses<0: raise ValueError("risk limits cannot be negative")

@dataclass(frozen=True, slots=True)
class RiskSnapshot:
    daily_loss: Decimal=Decimal(0)
    drawdown: Decimal=Decimal(0)
    consecutive_losses: int=0
    position_notional: Decimal=Decimal(0)
    kill_switch: KillSwitchState=KillSwitchState.INACTIVE
    data_healthy: bool=True
    broker_healthy: bool=True
    reconciled: bool=True

class RiskManager:
    def __init__(self, limits: RiskLimits): self.limits=limits
    def evaluate(self, intent, *, risk_amount: Decimal, snapshot: RiskSnapshot, now, decision_id: str) -> RiskDecision:
        reasons=[]
        if snapshot.kill_switch is KillSwitchState.ACTIVE: reasons.append(ReasonCode.KILL_SWITCH_ACTIVE.value)
        if not snapshot.data_healthy: reasons.append("DATA_UNHEALTHY")
        if not snapshot.broker_healthy: reasons.append("BROKER_UNHEALTHY")
        if not snapshot.reconciled: reasons.append("RECONCILIATION_INCOMPLETE")
        if risk_amount > self.limits.max_trade_risk: reasons.append("TRADE_RISK_LIMIT_EXCEEDED")
        if snapshot.daily_loss >= self.limits.max_daily_loss and self.limits.max_daily_loss>0: reasons.append("DAILY_LOSS_LIMIT_EXCEEDED")
        if snapshot.drawdown >= self.limits.max_drawdown and self.limits.max_drawdown>0: reasons.append("DRAWDOWN_LIMIT_EXCEEDED")
        if snapshot.consecutive_losses >= self.limits.max_consecutive_losses and self.limits.max_consecutive_losses>0: reasons.append("CONSECUTIVE_LOSS_LIMIT_EXCEEDED")
        if snapshot.position_notional+abs(intent.quantity*(intent.limit_price or Decimal(0))) > self.limits.max_position_notional: reasons.append("POSITION_NOTIONAL_LIMIT_EXCEEDED")
        if reasons: return RiskDecision(decision_id,intent.intent_id,RiskDecisionStatus.RISK_OFF if any(x in reasons for x in (ReasonCode.KILL_SWITCH_ACTIVE.value,"DATA_UNHEALTHY","BROKER_UNHEALTHY","RECONCILIATION_INCOMPLETE")) else RiskDecisionStatus.REJECTED,now,tuple(dict.fromkeys(reasons)))
        return RiskDecision(decision_id,intent.intent_id,RiskDecisionStatus.APPROVED,now,(ReasonCode.ACCEPTED.value,))
