from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.application.backtest_replay import ReplayCosts, run_replay, walk_forward
from app.application.strategy_manager import MTFTrendPullbackContinuation, StrategyManager, StrategySpec
from app.backtest.bars import Bar


def bars(n=80):
    start=datetime(2026,1,1,tzinfo=timezone.utc)
    out=[]
    price=Decimal("100")
    for i in range(n):
        end=start+timedelta(minutes=5*(i+1))
        nxt=price+Decimal("0.10")
        out.append(Bar(end-timedelta(minutes=5),end,price,nxt+Decimal("0.10"),price-Decimal("0.05"),nxt,Decimal("100000"),100))
        price=nxt
    return out


def manager():
    return StrategyManager(MTFTrendPullbackContinuation(StrategySpec(
        "mtf_test_v1","1.0","daily_trading",True,"test"
    )))


def test_replay_is_deterministic_and_never_live():
    result1=run_replay(bars(),[manager()],symbol="TEST",costs=ReplayCosts())
    result2=run_replay(bars(),[manager()],symbol="TEST",costs=ReplayCosts())
    assert result1.metrics == result2.metrics
    assert all(t.entry_time < (t.exit_time or t.entry_time) for t in result1.trades)


def test_costs_reduce_or_equal_net_pnl():
    gross=run_replay(bars(),[manager()],symbol="TEST",costs=ReplayCosts())
    costly=run_replay(bars(),[manager()],symbol="TEST",costs=ReplayCosts(commission_bps=2,spread_bps=2,slippage_bps=2,fee_bps=1))
    assert costly.metrics["total_pnl"] <= gross.metrics["total_pnl"]


def test_walk_forward_returns_oos_windows():
    reports=walk_forward(bars(100),[manager()],train_bars=40,test_bars=20)
    assert len(reports)==3
