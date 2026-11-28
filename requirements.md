# Trading System Requirements

## 1. Objective

Build a selective, deterministic multi-market intraday trading platform that continuously scans supported markets but trades only when a statistically validated, high-quality opportunity exists.

Primary goals:
- Maximize expectancy and profit factor.
- Target win rate >=65% without forcing trades.
- Target profit factor >=1.5; stretch target >=2.0.
- Minimize drawdown and preserve capital.
- Treat NO_TRADE as a first-class and preferred outcome.
- The 2% daily return objective for a ₹25,000 account is an aspiration, never a forced quota or guarantee.

## 2. Initial Markets

Broker/execution connectivity is assumed for Interactive Brokers across its supported markets and Binance for crypto.

Initial trading universe should be deliberately narrow and liquid:
- US equities: major liquid blue-chip stocks and broad/liquid ETFs.
- India equities: major liquid large-cap/blue-chip stocks and major indices/ETFs where supported.
- Gold: highly liquid gold instruments.
- Oil: highly liquid WTI/Brent instruments.
- Crypto: BTC and ETH initially.

The platform may observe additional markets but must rank and exclude instruments with inadequate liquidity, spread, volatility quality, historical edge, or execution quality.

## 3. Market Data

Start locally using free/low-cost market data sources such as Yahoo Finance, Google Finance and other permitted providers, plus crypto market-data providers.

The data layer must normalize:
- ticks/trades/quotes where available;
- timestamps and time zones;
- OHLCV bars;
- sessions/calendars;
- corporate actions where relevant;
- missing/duplicate/out-of-order data;
- feed health and latency.

Free feeds are for research, simulation and paper trading. Before live execution, executable broker/exchange quotes must be authoritative for order validation.

## 4. Trading Decision Chain

A candidate trade must pass, in order:

1. Instrument liquidity/quality gate.
2. Market regime gate.
3. Market/sector/asset-group alignment.
4. Relative strength/weakness.
5. 30m primary trend.
6. 15m trend/structure confirmation.
7. 5m actionable setup.
8. Fibonacci swing/retracement context.
9. DMA alignment.
10. Volume confirmation.
11. Momentum context, including overbought/oversold.
12. Two-bar 5m price-action confirmation.
13. Sufficient move room and risk/reward.
14. Portfolio/risk approval.
15. Execution-quality approval.

No single indicator may independently create a trade.

## 5. Fib/DMA Proximity

Fib levels and DMA values are zones, not exact prices.

Do NOT hard-code a 2–5% tolerance. Backtest and validate:
- percentage distance;
- ATR-normalized distance;
- instrument-specific tolerance;
- volatility/regime-specific tolerance.

Choose tolerances using out-of-sample validation to maximize risk-adjusted expectancy/profit factor while controlling drawdown and avoiding excessive trade frequency.

Never widen tolerance merely to manufacture trades.

## 6. Setup and Trigger

Separate SETUP from TRIGGER.

Example long setup:
- bullish 30m/15m trend;
- 5m controlled pullback;
- valid Fib/DMA proximity;
- supportive volume behavior;
- sensible momentum context.

Trigger requires two-bar 5m confirmation, preferably with rejection/structure confirmation and volume expansion.

No confirmation = NO_TRADE.

## 7. Trade Quality

Every candidate receives a deterministic quality score using:
- market regime;
- market/sector alignment;
- relative strength;
- multi-timeframe trend;
- Fib/DMA proximity;
- volume;
- momentum;
- price action;
- liquidity;
- expected slippage;
- risk/reward;
- current portfolio exposure.

Thresholds must be empirically validated.

## 8. Risk and Portfolio Control

All trades require pre-trade:
- entry;
- stop;
- expected target/move;
- maximum loss;
- position size;
- expected slippage;
- portfolio impact.

Position size is risk-based, not cash-based.

Initial research guardrails:
- 0.25–0.50% account risk per trade;
- approximately 1% daily loss limit;
- strict maximum gross/net exposure;
- strict correlated exposure limits.

Hard circuit breakers:
- daily loss;
- consecutive losses;
- abnormal volatility;
- spread/slippage;
- data-quality failure;
- broker/API failure;
- excessive correlated exposure;
- emergency kill switch.

## 9. Trade Management

Support:
- protective stops;
- partial profit taking;
- structure-based trailing;
- trend-change exits;
- maximum holding time;
- session flattening.

The system must not rely solely on fixed profit targets.

## 10. Execution Safety

Execution must be separated from signal generation.

Support:
- pre-trade validation;
- idempotent order submission;
- client/order correlation IDs;
- order state machine;
- acknowledgement/rejection handling;
- partial fills;
- cancel/replace;
- reconciliation;
- duplicate-order prevention;
- broker/exchange disconnect recovery.

Live execution must be disabled by default until explicitly enabled.

## 11. Simulation and Backtesting

Provide:
- deterministic historical backtest engine;
- event/tick replay;
- bar replay;
- paper trading;
- mock broker/trade API;
- realistic commissions, fees, spread and slippage;
- partial fills;
- order latency;
- rejected orders;
- market/session calendars.

Backtests must be causal: no future information, look-ahead, or leakage.

Validate across bull, bear, sideways, choppy, high/low volatility and multiple historical periods.

Report:
- trade count;
- win rate;
- profit factor;
- expectancy;
- average win/loss;
- max drawdown;
- consecutive losses;
- exposure;
- slippage sensitivity;
- performance by market, symbol, regime, setup and score;
- rejection reasons.

## 12. UI

Provide a web UI for:
- system health;
- market watchlist;
- live/recorded prices;
- regime state;
- candidate signals;
- signal drill-down;
- strategy state;
- portfolio/equity;
- positions;
- orders;
- fills;
- P&L;
- risk limits;
- rejected trades and reasons;
- backtest results;
- audit/event timeline;
- manual execution where explicitly enabled;
- emergency kill switch.

## 13. Cloud Readiness

The platform must run locally first but be deployable later as a dedicated container on AWS, Azure or another container platform.

Requirements:
- containerized services;
- configuration through environment/config files;
- no hard-coded local paths;
- stateless services where practical;
- persistent state isolated behind repositories;
- health/readiness endpoints;
- structured logs;
- metrics/tracing hooks;
- secrets supplied externally;
- clean separation of domain, infrastructure and UI;
- deterministic local development scripts.

## 14. Design Principles

Apply established engineering principles:
- SOLID;
- separation of concerns;
- dependency inversion;
- hexagonal/ports-and-adapters architecture;
- domain-driven boundaries where useful;
- event-driven design;
- explicit state machines;
- idempotency;
- fail-closed safety;
- immutable/auditable events where practical;
- observability;
- least privilege;
- testability;
- deterministic reproducibility.

## 15. Safety Rule

The system must prefer:

NO_TRADE > BAD_TRADE.

No component may bypass portfolio/risk controls merely to increase trade count or backtest activity.
