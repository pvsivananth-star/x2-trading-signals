from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.calendar import WeekendCalendar
from app.domain.clock import ReplayClock
from app.domain.entities import Bar, Instrument
from app.domain.event_envelope import EventEnvelope
from app.domain.portfolio.model import DAILY_TRADING_CONTEXT, LONG_TERM_CONTEXT, PortfolioAllocation
from app.domain.reason_codes import ReasonCode
from app.domain.runtime import RuntimeMode

def test_event_envelope_is_utc_and_idempotent():
    t=datetime(2026,1,2,10,tzinfo=UTC)
    e=EventEnvelope.create(event_type="BarClosed",aggregate_id="AAPL",occurred_at=t,
        observed_at=t+timedelta(seconds=1),causal_cutoff=t,producer="test",producer_version="1")
    assert e.occurred_at.tzinfo == UTC
    assert e.idempotency_key == str(e.event_id)

def test_event_envelope_rejects_bad_cutoff():
    t=datetime(2026,1,2,10,tzinfo=UTC)
    with pytest.raises(ValueError):
        EventEnvelope.create(event_type="BarClosed",aggregate_id="AAPL",occurred_at=t,
            observed_at=t,causal_cutoff=t-timedelta(seconds=1),producer="test",producer_version="1")

def test_replay_clock_is_monotonic():
    t=datetime(2026,1,2,10,tzinfo=UTC)
    clock=ReplayClock([t,t+timedelta(minutes=5)])
    assert clock.advance()==t
    assert clock.advance()>t

def test_replay_clock_rejects_backward_time():
    t=datetime(2026,1,2,10,tzinfo=UTC)
    clock=ReplayClock([t,t-timedelta(seconds=1)])
    clock.advance()
    with pytest.raises(ValueError):
        clock.advance()

def test_portfolio_contexts_are_distinct_and_allocations_immutable():
    assert LONG_TERM_CONTEXT.portfolio_id.value=="long_term"
    assert DAILY_TRADING_CONTEXT.portfolio_id.value=="daily_trading"
    with pytest.raises(ValueError):
        PortfolioAllocation(LONG_TERM_CONTEXT.portfolio_id,Decimal("-1"),Decimal("1"),Decimal("1"),Decimal("1"))

def test_domain_invariants():
    instrument=Instrument("AAPL","equity","NASDAQ")
    t=datetime(2026,1,2,10,tzinfo=UTC)
    bar=Bar(instrument,"5m",t,Decimal("10"),Decimal("12"),Decimal("9"),Decimal("11"),Decimal("100"))
    assert bar.close==Decimal("11")
    with pytest.raises(ValueError):
        Bar(instrument,"5m",t,Decimal("10"),Decimal("9"),Decimal("12"),Decimal("11"),Decimal("100"))
    assert ReasonCode.NO_TRADE.value=="NO_TRADE"

def test_runtime_modes_never_enable_live_broker():
    assert not RuntimeMode.LIVE_DISABLED.allows_live_broker
    assert not RuntimeMode.BACKTEST.allows_live_broker
    assert RuntimeMode.PAPER.allows_order_submission
    assert not WeekendCalendar().is_open("NYSE",datetime(2026,1,3,tzinfo=UTC))
