"""Trade Manager: converts strategy candidates into controlled TradeIntents."""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from app.application.strategy_manager import StrategyDecision
from app.domain.trades.model import TradeIntent, TradeIntentStatus

@dataclass(frozen=True, slots=True)
class TradeManager:
    def create_intent(self, decision: StrategyDecision, *, quantity: Decimal, now, limit_price=None, stop_price=None, target_price=None) -> TradeIntent:
        if not decision.trade_candidate: raise ValueError("only TRIGGER decisions can create trade intents")
        side="BUY" if decision.side.value=="LONG" else "SELL"
        return TradeIntent(
            intent_id=f"TI-{decision.signal.signal_id}",
            symbol=decision.symbol, side=side, quantity=quantity,
            portfolio_id=decision.portfolio_id, strategy_id=decision.strategy_id,
            strategy_version=decision.strategy_version, signal_id=decision.signal.signal_id,
            created_at=now, limit_price=limit_price, stop_price=stop_price,
            target_price=target_price, reason_codes=decision.reason_codes,
        )
