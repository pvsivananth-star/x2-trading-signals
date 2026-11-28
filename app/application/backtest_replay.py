"""Unified deterministic replay/backtest pipeline for Phase 6.

The replay path uses the same StrategyManager/PortfolioController/RiskManager/
ExecutionManager contracts as paper execution. It never enables live routing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Iterable, Sequence

from app.application.analyzers.core import AnalyzerContext, RelativeStrengthAnalyzer
from app.application.execution import ExecutionManager, InMemoryExecutionAdapter
from app.application.portfolio_controller import PortfolioController, PortfolioLimits, PortfolioSnapshot
from app.application.risk_manager import RiskLimits, RiskManager, RiskSnapshot
from app.application.strategy_manager import StrategyContext, StrategyManager
from app.application.trades import TradeManager
from app.backtest.bars import Bar, rollup
from app.domain.risk.model import RiskDecisionStatus
from app.domain.runtime import RuntimeMode


@dataclass(frozen=True, slots=True)
class ReplayCosts:
    commission_bps: Decimal = Decimal("0")
    spread_bps: Decimal = Decimal("0")
    slippage_bps: Decimal = Decimal("0")
    fee_bps: Decimal = Decimal("0")
    latency_bars: int = 1

    def __post_init__(self):
        if min(self.commission_bps, self.spread_bps, self.slippage_bps, self.fee_bps) < 0:
            raise ValueError("costs cannot be negative")
        if self.latency_bars < 0:
            raise ValueError("latency_bars cannot be negative")


@dataclass(frozen=True, slots=True)
class ReplayTrade:
    strategy_id: str
    portfolio_id: str
    symbol: str
    side: str
    entry_time: datetime
    entry_price: Decimal
    exit_time: datetime | None
    exit_price: Decimal | None
    quantity: Decimal
    gross_pnl: Decimal
    total_cost: Decimal
    net_pnl: Decimal
    score: int
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReplayReport:
    trades: tuple[ReplayTrade, ...]
    equity_curve: tuple[tuple[datetime, Decimal], ...]
    rejected: int
    metrics: dict[str, object]


@dataclass
class _OpenTrade:
    strategy_id: str
    portfolio_id: str
    symbol: str
    side: str
    entry_time: datetime
    entry_price: Decimal
    quantity: Decimal
    score: int
    reasons: tuple[str, ...]
    risk_amount: Decimal


def _pct_cost(notional: Decimal, bps: Decimal) -> Decimal:
    return notional * bps / Decimal("10000")


def _metrics(pnls: Sequence[Decimal], start_equity: Decimal) -> dict[str, object]:
    if not pnls:
        return {"trades": 0, "win_rate": 0.0, "profit_factor": None, "total_pnl": 0.0, "max_drawdown_pct": 0.0}
    wins=[p for p in pnls if p > 0]
    losses=[p for p in pnls if p < 0]
    gross_win=sum(wins, Decimal(0)); gross_loss=-sum(losses, Decimal(0))
    equity=start_equity; peak=equity; max_dd=Decimal(0)
    for pnl in pnls:
        equity += pnl
        peak=max(peak,equity)
        if peak: max_dd=max(max_dd,(peak-equity)/peak)
    return {
        "trades": len(pnls),
        "win_rate": round(len(wins)/len(pnls),4),
        "profit_factor": float(gross_win/gross_loss) if gross_loss else None,
        "total_pnl": float(sum(pnls,Decimal(0))),
        "return_pct": float(sum(pnls,Decimal(0))/start_equity*100) if start_equity else 0.0,
        "max_drawdown_pct": float(max_dd*100),
    }


def _features(bars: Sequence[Bar], as_of: datetime) -> dict[str, object]:
    out={}
    for seconds, tf in ((300,"5m"),(900,"15m"),(1800,"30m"),(86400,"1d"),(604800,"1W")):
        out[tf]=__import__("app.features",fromlist=["feature_snapshot"]).feature_snapshot(
            [b for b in bars if b.bar_end <= as_of and b.bar_end > as_of-timedelta(seconds=seconds*250)],
            as_of,
        )
    return out


def run_replay(
    bars: Sequence[Bar],
    strategies: Sequence[StrategyManager],
    *,
    symbol: str,
    start_equity: Decimal = Decimal("25000"),
    risk_per_trade: Decimal = Decimal("0.0025"),
    costs: ReplayCosts = ReplayCosts(),
) -> ReplayReport:
    """Replay closed bars only; entries occur no earlier than the next bar."""
    ordered=sorted((b for b in bars if b.final), key=lambda b:b.bar_end)
    if not ordered:
        return ReplayReport((),(),0,{"trades":0})
    portfolio=PortfolioController(PortfolioLimits(start_equity,start_equity*risk_per_trade,start_equity,start_equity,start_equity*risk_per_trade*2))
    risk=RiskManager(RiskLimits(max_trade_risk=start_equity*risk_per_trade,max_daily_loss=start_equity*Decimal("0.02"),max_drawdown=Decimal("0.05"),max_consecutive_losses=5,max_position_notional=start_equity))
    execution=ExecutionManager(InMemoryExecutionAdapter(),runtime_mode=RuntimeMode.PAPER)
    trade_manager=TradeManager()
    open_trades: dict[str,_OpenTrade]={}
    closed:list[ReplayTrade]=[]
    equity=start_equity
    curve=[]; rejected=0
    pending:list[tuple[int,StrategyManager,object]]= []

    for i,bar in enumerate(ordered):
        as_of=bar.bar_end
        # deterministic next-bar execution
        due=[x for x in pending if x[0] <= i]
        pending=[x for x in pending if x[0] > i]
        for _,manager,decision in due:
            if not decision.trade_candidate: continue
            price=bar.open
            notional=price*Decimal("1")
            intent=trade_manager.create_intent(decision,quantity=Decimal("1"),now=as_of,limit_price=price)
            pdecision=portfolio.evaluate(intent,PortfolioSnapshot(equity,Decimal(0),Decimal(0),Decimal(0),Decimal(0)),risk_amount=equity*risk_per_trade,expected_notional=notional,reservation_id=intent.intent_id)
            if not pdecision.approved:
                rejected+=1; continue
            rsnapshot=RiskSnapshot(Decimal(0),Decimal(0),0,notional,False,True,True)
            rdecision=risk.evaluate(intent,risk_amount=equity*risk_per_trade,snapshot=rsnapshot,now=as_of,decision_id=f"RD-{intent.intent_id}")
            result=execution.submit(intent,portfolio_approved=pdecision.approved,risk_status=rdecision.status,timestamp=as_of)
            if result.accepted:
                open_trades[intent.intent_id]=_OpenTrade(manager.spec.strategy_id,manager.spec.portfolio_id,symbol,intent.side,as_of,price,Decimal("1"),decision.score,decision.reason_codes,equity*risk_per_trade)
            else:
                rejected+=1

        for intent_id,trade in list(open_trades.items()):
            # Conservative exit model: opposite strategy trigger or end of replay.
            if i == len(ordered)-1:
                exit_price=bar.close
            else:
                continue
            gross=(exit_price-trade.entry_price)*(Decimal("1") if trade.side=="BUY" else Decimal("-1"))*trade.quantity
            entry_notional=trade.entry_price*trade.quantity
            exit_notional=exit_price*trade.quantity
            total_cost=(_pct_cost(entry_notional,costs.commission_bps+costs.spread_bps+costs.slippage_bps+costs.fee_bps)
                        +_pct_cost(exit_notional,costs.commission_bps+costs.spread_bps+costs.slippage_bps+costs.fee_bps))
            net=gross-total_cost
            closed.append(ReplayTrade(trade.strategy_id,trade.portfolio_id,symbol,trade.side,trade.entry_time,trade.entry_price,as_of,exit_price,trade.quantity,gross,total_cost,net,trade.score,trade.reasons))
            equity += net
            del open_trades[intent_id]

        features=_features(ordered,as_of)
        analyzers={"relative_strength":RelativeStrengthAnalyzer().analyze(AnalyzerContext(symbol,as_of,{"30m":[b for b in ordered if b.bar_end<=as_of]}))}
        context=StrategyContext(symbol,as_of,as_of,features,analyzers,{"replay":"phase6","causal_cutoff":as_of.isoformat()})
        for manager in strategies:
            decision=manager.evaluate(context)
            if decision.trade_candidate:
                pending.append((i+max(1,costs.latency_bars),manager,decision))
        curve.append((as_of,equity))

    return ReplayReport(tuple(closed),tuple(curve),rejected,_metrics([t.net_pnl for t in closed],start_equity))


def walk_forward(
    bars: Sequence[Bar],
    strategies: Sequence[StrategyManager],
    *,
    train_bars: int,
    test_bars: int,
    start_equity: Decimal = Decimal("25000"),
    costs: ReplayCosts = ReplayCosts(),
) -> tuple[ReplayReport,...]:
    if train_bars <= 0 or test_bars <= 0:
        raise ValueError("train_bars and test_bars must be positive")
    ordered=sorted(bars,key=lambda b:b.bar_end)
    reports=[]
    cursor=train_bars
    while cursor < len(ordered):
        test=ordered[cursor:min(cursor+test_bars,len(ordered))]
        if test:
            reports.append(run_replay(test,strategies,start_equity=start_equity,costs=costs))
        cursor += test_bars
    return tuple(reports)
