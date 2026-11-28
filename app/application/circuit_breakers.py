"""Fail-closed circuit breaker and emergency kill switch."""
from dataclasses import dataclass
from datetime import datetime, timezone
from app.domain.risk.model import KillSwitch, KillSwitchState

@dataclass
class CircuitBreaker:
    kill_switch: KillSwitch=KillSwitch()
    data_healthy: bool=True
    broker_healthy: bool=True
    reconciled: bool=True
    def activate(self, *, reason:str, operator:str, now:datetime|None=None):
        self.kill_switch=KillSwitch(KillSwitchState.ACTIVE,now or datetime.now(timezone.utc),reason,operator)
    def release(self, *, operator:str, now:datetime|None=None):
        if not self.data_healthy or not self.broker_healthy or not self.reconciled: raise RuntimeError("cannot release kill switch while safety conditions are unhealthy")
        self.kill_switch=KillSwitch(KillSwitchState.INACTIVE,now or datetime.now(timezone.utc),None,operator)
    def can_increase_risk(self)->bool:
        return self.kill_switch.state is KillSwitchState.INACTIVE and self.data_healthy and self.broker_healthy and self.reconciled
