"""Central portfolio gate: allocation, reservations and account-wide exposure."""
from __future__ import annotations
from dataclasses import dataclass, field
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
    def __post_init__(self):
        if any(x<0 for x in (self.capital_budget,self.risk_budget,self.max_gross_exposure,self.max_net_exposure,self.max_strategy_risk)): raise ValueError("limits cannot be negative")

@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    cash: Decimal
    reserved_capital: Decimal
    consumed_risk: Decimal
    gross_exposure: Decimal
    net_exposure: Decimal
    def available_capital(self): return max(Decimal(0), self.cash-self.reserved_capital)

@dataclass(frozen=True, slots=True)
class PortfolioDecision:
    approved: bool
    reason_codes: tuple[str,...]
    reservation_id: str|None=None
    reserved_capital: Decimal=Decimal(0)
    reserved_risk: Decimal=Decimal(0)

class PortfolioController:
    def __init__(self, limits: PortfolioLimits, *, account_max_gross: Decimal|None=None, account_max_net: Decimal|None=None):
        self.limits=limits; self.account_max_gross=account_max_gross or limits.max_gross_exposure; self.account_max_net=account_max_net or limits.max_net_exposure
        self._reservations: dict[str, tuple[TradeIntent,Decimal,Decimal]]= {}
    def evaluate(self, intent: TradeIntent, snapshot: PortfolioSnapshot, *, risk_amount: Decimal, expected_notional: Decimal, reservation_id: str):
        reasons=[]
        if intent.portfolio_id=="" or intent.strategy_id=="": reasons.append(ReasonCode.PORTFOLIO_REJECTED.value)
        if risk_amount<=0 or expected_notional<=0: reasons.append(ReasonCode.PORTFOLIO_REJECTED.value)
        if snapshot.available_capital() < expected_notional: reasons.append("INSUFFICIENT_AVAILABLE_CAPITAL")
        reserved_risk=sum(x[2] for x in self._reservations.values() if x[0].portfolio_id==intent.portfolio_id)
        if snapshot.consumed_risk+reserved_risk+risk_amount > self.limits.risk_budget: reasons.append("PORTFOLIO_RISK_BUDGET_EXCEEDED")
        reserved_gross=sum(x[1] for x in self._reservations.values() if x[0].portfolio_id==intent.portfolio_id)
        if snapshot.gross_exposure+reserved_gross+expected_notional > self.limits.max_gross_exposure: reasons.append("PORTFOLIO_GROSS_EXPOSURE_EXCEEDED")
        signed=expected_notional if intent.side=="BUY" else -expected_notional
        if abs(snapshot.net_exposure+signed)>self.limits.max_net_exposure: reasons.append("PORTFOLIO_NET_EXPOSURE_EXCEEDED")
        if snapshot.gross_exposure+reserved_gross+expected_notional > self.account_max_gross: reasons.append("ACCOUNT_GROSS_EXPOSURE_EXCEEDED")
        if abs(snapshot.net_exposure+signed)>self.account_max_net: reasons.append("ACCOUNT_NET_EXPOSURE_EXCEEDED")
        strategy_reserved_risk=sum(x[2] for x in self._reservations.values() if x[0].portfolio_id==intent.portfolio_id and x[0].strategy_id==intent.strategy_id)\n        if strategy_reserved_risk+risk_amount > self.limits.max_strategy_risk: reasons.append("STRATEGY_RISK_BUDGET_EXCEEDED")\n        if reasons: return PortfolioDecision(False,tuple(dict.fromkeys(reasons)))
        self._reservations[reservation_id]=(intent,expected_notional,risk_amount)
        return PortfolioDecision(True,(ReasonCode.ACCEPTED.value,),reservation_id,expected_notional,risk_amount)
    def release(self,reservation_id:str): self._reservations.pop(reservation_id,None)
