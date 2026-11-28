"""Risk decisions, reservations and kill-switch state."""
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from datetime import datetime

class RiskDecisionStatus(StrEnum):
    APPROVED="APPROVED"
    REJECTED="REJECTED"
    RISK_OFF="RISK_OFF"

class KillSwitchState(StrEnum):
    INACTIVE="INACTIVE"
    ACTIVE="ACTIVE"

@dataclass(frozen=True, slots=True)
class RiskDecision:
    decision_id: str
    intent_id: str
    status: RiskDecisionStatus
    timestamp: datetime
    reason_codes: tuple[str,...]
    reservation_id: str|None=None
    def __post_init__(self):
        if not self.reason_codes:
            raise ValueError("risk decision requires reason codes")

@dataclass(frozen=True, slots=True)
class RiskReservation:
    reservation_id: str
    portfolio_id: str
    strategy_id: str
    capital: Decimal
    risk: Decimal
    gross_exposure: Decimal
    net_exposure: Decimal
    created_at: datetime
    def __post_init__(self):
        if any(x < 0 for x in (self.capital,self.risk,self.gross_exposure,self.net_exposure)):
            raise ValueError("reservation amounts cannot be negative")

@dataclass(frozen=True, slots=True)
class KillSwitch:
    state: KillSwitchState=KillSwitchState.INACTIVE
    activated_at: datetime|None=None
    reason: str|None=None
    activated_by: str|None=None
