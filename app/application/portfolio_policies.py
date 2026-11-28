"""Portfolio-specific lifecycle policies."""
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

class PositionAction(StrEnum):
    HOLD="HOLD"; DE_RISK="DE_RISK"; EXIT="EXIT"; FLATTEN="FLATTEN"

@dataclass(frozen=True, slots=True)
class LongTermExitPolicy:
    def evaluate(self, *, weekly_trend:str, weekly_structure:str, thesis_valid:bool=True, liquidity_ok:bool=True, risk_breach:bool=False)->PositionAction:
        if risk_breach or not liquidity_ok or not thesis_valid: return PositionAction.EXIT
        if weekly_trend=="down" or weekly_structure=="lower-highs-lower-lows": return PositionAction.DE_RISK
        return PositionAction.HOLD

@dataclass(frozen=True, slots=True)
class DailySessionPolicy:
    flatten_minutes_before_close:int=15
    def should_flatten(self, *, minutes_to_close:int|None, session_open:bool=True)->bool:
        return session_open and minutes_to_close is not None and minutes_to_close <= self.flatten_minutes_before_close
    def can_open(self, *, minutes_to_close:int|None, entry_cutoff_minutes:int=60, session_open:bool=True)->bool:
        return session_open and (minutes_to_close is None or minutes_to_close > entry_cutoff_minutes)

@dataclass(frozen=True, slots=True)
class PositionRisk:
    entry: Decimal
    stop: Decimal
    quantity: Decimal
    def risk_amount(self)->Decimal:
        return abs(self.entry-self.stop)*abs(self.quantity)
