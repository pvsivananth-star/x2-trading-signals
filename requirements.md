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

## 1A. Non-Functional and Operational Requirements

The system must optimize for **capital preservation, deterministic decisions, auditability and operational correctness**, not maximum trade frequency.

Required guarantees:
- Every decision is reproducible from a versioned input/configuration snapshot.
- Every order has a complete lineage: market data -> features -> signal -> strategy decision -> trade intent -> risk decision -> order -> fills -> position.
- All state-changing commands are idempotent and safe to retry.
- Event processing must be ordered where causality requires it and must tolerate duplicate delivery.
- No decision may use data newer than its causal cutoff.
- Configuration, strategy code/version, risk-policy version and data-source provenance must be captured for every decision.
- Failures in data, broker connectivity, persistence or reconciliation fail closed for new risk-taking.
- Operational recovery must not create duplicate orders or silently lose fills.

## 1B. Portfolio Accounting and Capital Reservation

Capital is allocated explicitly to each portfolio and strategy. Risk budgets are **reservations**, not merely reporting limits.

Before execution, the system must reserve the required risk/exposure budget. Reservations are released or adjusted only from authoritative order/fill/position state.

The Portfolio Controller must support:
- cash and buying-power accounting;
- reserved versus available capital;
- reserved versus consumed risk;
- realized/unrealized P&L;
- currency/FX exposure;
- gross and net exposure;
- cross-strategy netting and correlated exposure;
- portfolio-to-account allocation;
- explicit capital transfer between portfolios only through an authorized workflow.

A rejected/cancelled/expired order must release its reservation deterministically.

## 2. Initial Markets

Broker/execution connectivity is assumed for Interactive Brokers across its supported markets and Binance for crypto.

Initial trading universe should be deliberately narrow and liquid:
- US equities: major liquid blue-chip stocks and broad/liquid ETFs.
- India equities: major liquid large-cap/blue-chip stocks and major indices/ETFs where supported.
- Gold: highly liquid gold instruments.
- Oil: highly liquid WTI/Brent instruments.
- Crypto: BTC and ETH initially.

The platform may observe additional markets but must rank and exclude instruments with inadequate liquidity, spread, volatility quality, historical edge, or execution quality.

## 2A. Instrument and Trading-Eligibility Governance

Each instrument must have a canonical identity and metadata including venue, currency, tick/lot size, price precision, trading hours, asset class, corporate-action status and shortability/borrow constraints where applicable.

The universe service must distinguish:
- observable;
- eligible for research;
- eligible for paper trading;
- eligible for live trading.

Eligibility must be based on deterministic liquidity, spread, data quality, execution quality and operational checks. Instrument onboarding/offboarding must be versioned and auditable.

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

## 7A. Decision Lineage and Configuration Versioning

Every signal, trade intent, risk decision and order decision must reference:
- strategy ID and immutable strategy version;
- portfolio ID;
- configuration/risk-policy version;
- feature/analyzer version;
- data-source and dataset version;
- causal market-data cutoff;
- correlation/causation IDs;
- deterministic reason codes.

A backtest run must persist the exact configuration and code/strategy version identifiers required to reproduce it.

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

## 9. Portfolio and Strategy Architecture

The system shall support multiple independent portfolios. Each portfolio has its own objective, capital/risk budget, eligible instruments, holding period, timeframe policy, exit policy and assigned strategies.

### 9.1 Long-Term Portfolio

Purpose: preserve and compound capital through major market trends rather than maximize daily trade count.

Decision timeframe:
- Primary: 1W.
- Optional confirmation: 1D.
- Lower timeframes may be used only for execution quality, never to override the weekly regime.

Initial strategy set:
1. **Weekly Trend Following** — participate in established weekly trends using weekly structure, moving-average/DMA alignment, breakout/pullback confirmation and volatility-aware stops.
2. **Weekly Breakout/Pullback Accumulation** — add/re-enter after validated weekly breakouts or controlled pullbacks when the major trend remains intact.

Exit policy:
- Do not liquidate the portfolio merely because a short-term chart weakens.
- Liquidate or materially reduce only the positions affected by a validated weekly trend reversal, structural failure, thesis invalidation, liquidity deterioration or portfolio-level risk breach.
- Support partial de-risking before full liquidation.
- Use position-level trailing/structure exits rather than a blanket portfolio exit.

The long-term portfolio must not be allowed to consume capital reserved for the Daily Trading Portfolio.

### 9.2 Daily Trading Portfolio

Purpose: selective intraday trading using short-range charts. The system may target approximately 2% overall daily return as an aspiration, but must never force trades, increase risk, or relax quality gates to achieve that target.

Decision timeframes:
- 30m: primary intraday trend/regime.
- 15m: structure and confirmation.
- 5m: setup and trigger.
- 1m or tick data may be used only for execution refinement where available.

Initial strategy set:
1. **MTF Trend Pullback Continuation** — 30m/15m aligned trend, controlled 5m pullback into validated Fib/DMA/structure zones, followed by two-bar 5m trigger and volume confirmation.
2. **VWAP/Relative-Strength Continuation** — trade liquid instruments aligned with market/sector direction when price reclaims/holds VWAP with relative-strength and volume confirmation.
3. **Opening-Range Breakout with Trend Filter** — trade only liquid instruments with strong opening participation when the breakout agrees with 30m/15m regime, relative strength and volume; reject weak or extended breakouts.

Strategy selection must be regime-aware. Mean-reversion behavior may be introduced later only after separate out-of-sample validation; it must not be mixed into the initial trend-following strategies.

### 9.3 Strategy Isolation

Every strategy is an independently versioned strategy instance with its own **Strategy Manager**. A Strategy Manager owns only one strategy's lifecycle, configuration, state, signals, metrics and decisions.

A portfolio may contain multiple Strategy Managers. Strategy Managers must not directly place orders or mutate another strategy's state. All strategy outputs converge through the Trade Manager, Portfolio Controller and Risk Manager.

The system must support enabling/disabling, versioning, parameterizing and backtesting each strategy independently and in portfolio combinations.

## 9A. Position and Exposure Semantics

Positions are authoritative account/portfolio state derived from fills, not from strategy intent.

The system must define how exposure is handled when multiple strategies recommend the same instrument:
- strategy attribution remains separate;
- economic exposure is consolidated before portfolio/account risk checks;
- exits cannot accidentally increase exposure;
- opposing strategy recommendations are resolved by explicit portfolio policy;
- long-term and daily positions remain isolated unless an explicit transfer is approved.

Trade lifecycle state must distinguish intent, submitted orders, fills and resulting position state.

## 10. Trade Management

Support:
- protective stops;
- partial profit taking;
- structure-based trailing;
- trend-change exits;
- maximum holding time;
- session flattening.

The system must not rely solely on fixed profit targets.

## 10A. Reconciliation and Recovery

Broker/exchange state is the external execution authority after order submission. The system must periodically reconcile:
- open orders;
- fills;
- positions;
- cash/buying power;
- fees;
- order status.

On reconciliation mismatch, the system must:
1. stop new risk-increasing orders for the affected scope;
2. reconcile deterministically;
3. record the discrepancy and resolution;
4. resume only when policy permits.

Startup recovery must rebuild operational state from durable state plus broker reconciliation; in-memory state must never be the sole source of truth.

## 11. Execution Safety

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

## 12. Simulation and Backtesting

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

## 13. UI

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

## 14A. Configuration, Secrets and Change Governance

Strategy parameters, risk limits, universe membership and execution policies are configuration data with explicit versions and effective timestamps.

Changes must be:
- validated before activation;
- auditable;
- attributable to an operator/system identity;
- reversible;
- prohibited from silently changing historical backtests.

Live-sensitive changes should require explicit authorization and, where configured, a two-step approval.

## 14. Cloud Readiness

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

## 15. Design Principles

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

## 15A. Reliability and Failure Domains

The system must define behavior for:
- stale/missing market data;
- out-of-order events;
- duplicate events;
- event-bus restart;
- database outage;
- broker disconnect;
- process restart during order submission;
- partial fills during disconnect;
- clock drift;
- market halt/circuit breaker;
- configuration corruption.

The default response to uncertainty is to stop new risk-taking and preserve/reconcile existing state.

## 16. Safety Rule

The system must prefer:

NO_TRADE > BAD_TRADE.

No component may bypass portfolio/risk controls merely to increase trade count or backtest activity.
