from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from app.application.signals import SignalManager, SignalSide
from app.application.strategy_manager import (
    MTFTrendPullbackContinuation, StrategyContext, StrategySpec,
    WeeklyTrendFollowing,
)
from app.application.strategy_registry import default_registry


def snap(**kwargs):
    defaults = dict(
        trend="unknown", structure="range", last_close=Decimal("100"),
        swing_high=Decimal("101"), swing_low=Decimal("99"), pullback_depth=Decimal("0.5"),
        vwap=Decimal("99"), rvol=Decimal("1.5"),
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def context(features, analyzers=None):
    t = datetime(2026, 1, 5, 15, tzinfo=UTC)
    return StrategyContext("TEST", t, t, features, analyzers or {}, {"data": "fixture"})


def test_weekly_strategy_is_long_term_only():
    strategy = WeeklyTrendFollowing(StrategySpec("weekly_trend_following_v1", "1.0.0", "long_term", True, "test"))
    d = strategy.evaluate(context({"1W": snap(trend="up", structure="higher-highs-higher-lows"), "1d": snap(trend="up")}))
    assert d.trade_candidate
    assert d.portfolio_id == "long_term"


def test_mtf_strategy_requires_all_three_timeframes():
    strategy = MTFTrendPullbackContinuation(StrategySpec("mtf_trend_pullback_v1", "1.0.0", "daily_trading", True, "test"))
    d = strategy.evaluate(context({
        "30m": snap(trend="up"), "15m": snap(trend="up", structure="higher-highs-higher-lows"),
        "5m": snap(trend="up", rvol=Decimal("1.2"))
    }))
    assert d.trade_candidate


def test_signal_is_deterministic_and_immutable():
    manager = SignalManager()
    t = datetime(2026, 1, 5, 15, tzinfo=UTC)
    analyzers = {"trend": SimpleNamespace(passed=True, values={"trend": "up"}, reasons=())}
    a = manager.evaluate(symbol="TEST", timestamp=t, causal_cutoff=t, portfolio_id="daily_trading",
        strategy_id="x", strategy_version="1", config_version="c", analyzers=analyzers, side=SignalSide.LONG)[0]
    b = manager.evaluate(symbol="TEST", timestamp=t, causal_cutoff=t, portfolio_id="daily_trading",
        strategy_id="x", strategy_version="1", config_version="c", analyzers=analyzers, side=SignalSide.LONG)[0]
    assert a.signal_id == b.signal_id and a.dedupe_key == b.dedupe_key
    try:
        a.score = 1
        assert False
    except AttributeError:
        pass


def test_registry_has_isolated_portfolios():
    r = default_registry()
    assert {m.spec.portfolio_id for m in r.for_portfolio("long_term")} == {"long_term"}
    assert {m.spec.portfolio_id for m in r.for_portfolio("daily_trading")} == {"daily_trading"}
    assert len(r.ids()) == 5
