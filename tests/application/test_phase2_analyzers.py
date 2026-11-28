from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from app.application.analyzers.core import AnalyzerContext, DMAAnalyzer, MomentumAnalyzer, TrendAnalyzer
def bars(n=60):
    t=datetime(2026,1,2,14,tzinfo=UTC)
    return [SimpleNamespace(bar_start=t+timedelta(minutes=5*i),bar_end=t+timedelta(minutes=5*(i+1)),open=Decimal(100+i),high=Decimal(101+i),low=Decimal(99+i),close=Decimal(100+i),volume=Decimal(100000+i*1000)) for i in range(n)]
def test_analyzers_are_causal():
    bs=bars(); t=bs[-1].bar_end
    ctx=AnalyzerContext("TEST",t,{"5m":bs,"15m":bs,"30m":bs})
    assert TrendAnalyzer().analyze(ctx).passed
    assert DMAAnalyzer().analyze(ctx).passed
    assert MomentumAnalyzer().analyze(ctx).passed
