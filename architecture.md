# Trading System Architecture

## 1. Architectural Goal

Create a modular trading platform with the same domain logic used by:
- historical backtests;
- local simulation;
- mock broker;
- paper trading;
- live IBKR/Binance execution.

Only adapters should change between environments.

## 2. High-Level Architecture

```
Market Data Providers
    |  Yahoo / Google / Crypto / IBKR / Binance
    v
[Market Data Adapters]
    v
[Normalizer + Clock + Session/Calendar]
    v
[Event Bus]
    |
    +--> [Market/Regime Actors]
    |        +--> Trend Analyzers
    |        +--> Volatility Analyzer
    |        +--> Volume/RVOL Analyzer
    |        +--> Relative Strength Analyzer
    |        +--> Structure/Fibonacci Analyzer
    |        +--> DMA Analyzer
    |
    +--> [Strategy Registry / Portfolio Strategy Orchestrator]
    |        +--> [Long-Term Portfolio]
    |        |       +--> [Strategy Manager: Weekly Trend Following]
    |        |       +--> [Strategy Manager: Weekly Breakout/Pullback]
    |        |
    |        +--> [Daily Trading Portfolio]
    |                +--> [Strategy Manager: MTF Trend Pullback]
    |                +--> [Strategy Manager: VWAP/Relative Strength]
    |                +--> [Strategy Manager: Opening Range Breakout]
    |
    |        Each Strategy Manager owns exactly one strategy
    |        and produces explainable SignalCandidates
    |
    v
[Trade Manager]
    v
[Portfolio Manager] <--> [Risk Manager]
    v
[Execution Manager]
    v
[Broker/Exchange Adapter]
    |
    +--> Mock Trade API
    +--> IBKR
    +--> Binance

[Event Store / Audit Log] <--> [UI/API]
[Backtest Engine] --> same domain analyzers/strategy/risk/execution ports
[Emergency Kill Switch] --> Portfolio/Risk/Execution
```

## 3. Actors / Components

### Market Data Actor
Consumes provider data, normalizes it and publishes canonical market events.

### Instrument/Universe Actor
Maintains eligible instruments, liquidity metrics, trading sessions and market metadata.

### Regime Actor
Classifies market/asset regime and publishes regime state.

### Trend Analyzer
Calculates 30m/15m/5m trend and DMA alignment.

### Structure Analyzer
Finds causal swings, support/resistance, price-action structure and trend changes.

### Fibonacci Analyzer
Calculates retracement/extension zones and exposes ATR/percentage proximity metrics. Tolerance is configuration/data, not hard-coded strategy logic.

### Volume Analyzer
Calculates volume, RVOL/slot-RVOL, impulse/pullback/reversal volume behavior.

### Momentum Analyzer
Calculates RSI/other configured momentum context without turning indicators into standalone signals.

### Relative Strength Analyzer
Compares instrument against market, sector and peer benchmarks.

### Signal Manager
Combines analyzer outputs into immutable, explainable candidate signals. It must distinguish SETUP from TRIGGER.

### Strategy Registry / Portfolio Strategy Orchestrator
Maintains the catalogue of enabled strategies and maps strategies to portfolios. It does not contain strategy logic.

### Strategy Manager
There is one Strategy Manager instance per strategy. It owns exactly one strategy's lifecycle, configuration/version, state, signal generation, position/trade context, metrics and decision audit.

A Strategy Manager:
- consumes only the market/analyzer events required by its strategy;
- produces immutable, explainable candidate signals;
- cannot submit broker orders directly;
- cannot mutate another Strategy Manager;
- cannot bypass Portfolio/Risk controls;
- can be enabled/disabled and backtested independently;
- maintains isolated state so multiple strategies can run concurrently without cross-strategy contamination.

Portfolio-level orchestration combines independent strategy outputs only after each strategy has made its own decision. Conflicting strategy recommendations must be resolved by explicit portfolio/risk rules, never by hidden strategy-to-strategy coupling.

### Trade Manager
Converts an approved signal into a trade intent and manages trade lifecycle.

### Portfolio Manager
Maintains positions, cash, realized/unrealized P&L, exposure, correlation and portfolio state.

### Risk Manager
Provides independent pre-trade and ongoing risk approval. Strategy code cannot override it.

### Execution Manager
Owns order state machines, idempotency, retries, cancel/replace, partial fills and reconciliation.

### Broker/Exchange Connectors
Ports/adapters for:
- Mock Trade API;
- IBKR;
- Binance;
- future brokers/exchanges.

### Backtest Engine
Replays historical events through the same domain interfaces. It must not contain strategy-specific shortcuts that cannot exist in live mode.

### Paper Trading Engine
Uses live market data with simulated execution.

### Emergency Kill Switch
Independent safety control that can:
- stop new orders;
- cancel open orders;
- request flattening;
- place system in read-only mode.

Kill state must be fail-closed and auditable.

### Notification/Alert Manager
Publishes operational, risk and trade alerts.

### Audit/Event Store
Persist all significant events:
market snapshot references, signals, approvals/rejections, orders, fills, portfolio/risk decisions, configuration versions and kill-switch actions.


## 4A. Portfolio and Strategy Allocation

The platform initially defines two logically independent portfolios:

### Long-Term Portfolio
- Timeframe: 1W primary, 1D confirmation.
- Objective: capture major trends and protect long-term capital.
- Strategies: Weekly Trend Following; Weekly Breakout/Pullback Accumulation.
- Exit behavior: position-specific de-risking on weekly structural deterioration; full liquidation only for validated thesis/trend failure, liquidity failure or portfolio risk breach.
- Intraday signals must not force liquidation of long-term positions.

### Daily Trading Portfolio
- Timeframes: 30m trend, 15m structure, 5m setup/trigger.
- Objective: selective intraday expectancy with low drawdown and high-quality trade frequency. A 2% daily return is an aspiration, not a quota.
- Strategies: MTF Trend Pullback Continuation; VWAP/Relative-Strength Continuation; Opening-Range Breakout with Trend Filter.
- Positions are normally flattened according to the configured session policy and may not be carried into the long-term portfolio without an explicit portfolio transfer decision.

Each strategy is independently managed by one Strategy Manager instance. Portfolio membership is explicit configuration, not inferred from strategy names.

Portfolio isolation requirements:
- separate capital/risk budgets;
- separate exposure and concentration limits;
- separate strategy enablement;
- separate performance attribution;
- separate holding-period and exit policies;
- central Portfolio Controller still enforces account-wide hard limits across both portfolios.

No strategy may borrow another portfolio's risk budget without an explicit portfolio-level allocation decision.

## 4. Domain Model

Core entities/value objects:

- Instrument
- Market
- Quote/Tick
- Bar
- MarketSession
- Regime
- TrendState
- IndicatorSnapshot
- Swing
- FibonacciZone
- Signal
- TradeIntent
- RiskDecision
- Portfolio
- Position
- Order
- Fill
- ExecutionReport
- Strategy
- StrategyVersion
- StrategyManager
- PortfolioStrategyAssignment
- PortfolioAllocation
- RiskLimit
- KillSwitchState
- BacktestRun
- Event/AuditRecord

All important decisions must carry:
- timestamp;
- instrument;
- strategy/version;
- data snapshot/reference;
- decision;
- reason codes;
- relevant metrics.

## 5. Event-Driven Contracts

Canonical events should include:

- MarketTickReceived
- QuoteUpdated
- BarClosed
- RegimeUpdated
- TrendUpdated
- SignalCandidateCreated
- SignalApproved
- SignalRejected
- TradeIntentCreated
- RiskApproved
- RiskRejected
- OrderSubmitted
- OrderAcknowledged
- OrderRejected
- OrderPartiallyFilled
- OrderFilled
- OrderCancelled
- PositionChanged
- PortfolioRiskChanged
- KillSwitchActivated
- KillSwitchReleased
- BacktestStarted
- BacktestCompleted

Events should be versioned and correlation IDs propagated end-to-end.

## 6. Ports and Adapters

Domain/application code must depend on interfaces, not vendor SDKs.

Ports include:

- MarketDataPort
- ExecutionPort
- AccountPort
- HistoricalDataPort
- ClockPort
- CalendarPort
- EventBusPort
- EventStorePort
- NotificationPort
- ConfigurationPort

Adapters implement these ports for local files, mock APIs, Yahoo/Google/crypto feeds, IBKR and Binance.

## 7. Execution State Machine

Order lifecycle:

```
CREATED
 -> VALIDATING
 -> SUBMITTED
 -> ACKNOWLEDGED
 -> PARTIALLY_FILLED
 -> FILLED
 -> CANCEL_PENDING
 -> CANCELLED

Any terminal/rejected state must be reconciled with broker state.
```

Duplicate submissions must be prevented using idempotency keys.

## 8.1 Strategy/Portfolio Selection Rules

Strategy selection is deterministic and portfolio-scoped.

- Long-term strategies evaluate weekly trend/structure and may use daily confirmation.
- Daily strategies evaluate 30m/15m/5m context and must satisfy the existing liquidity, regime, Fib/DMA, volume and two-bar trigger gates.
- Strategies must declare supported instruments, timeframes, regimes, holding period, entry model, exit model and risk profile.
- The same market event may be evaluated by multiple strategies, but each Strategy Manager makes an independent decision.
- Duplicate economic exposure across strategies is consolidated by the Portfolio Controller before risk approval.

## 8. Risk Architecture

Risk checks occur independently at:
1. individual strategy/trade level;
2. portfolio level (Long-Term and Daily Trading independently);
3. account-wide level across all portfolios;
4. execution level;
5. broker/account reconciliation level.

Risk controls include:
- per-trade risk;
- daily loss;
- drawdown;
- position limits;
- notional limits;
- sector/asset correlation;
- concentration;
- liquidity;
- spread;
- slippage;
- market/session;
- API/data health.

Risk must fail closed.

## 9. Portfolio Control

A central Portfolio Controller must see every trade intent before execution.

It calculates:
- current exposure;
- post-trade exposure;
- correlated exposure;
- available risk budget;
- realized/unrealized P&L;
- drawdown;
- daily risk consumption.

It can reject an otherwise valid strategy signal.

## 10. Mock Trade API

Implement a local broker simulator exposing the same execution port as real brokers.

It must simulate:
- market/limit/stop orders;
- fills;
- partial fills;
- slippage;
- commissions/fees;
- latency;
- rejections;
- cancellations;
- disconnects;
- account/position queries.

This becomes the default local/demo execution backend.

## 11. Backtest Design

Backtest engine uses the same:
- event model;
- strategy interfaces;
- risk manager;
- portfolio manager;
- execution port;
- trade lifecycle.

Historical data is replayed causally.

Backtest-only behavior must be isolated in infrastructure adapters, never hidden inside strategy decisions.

## 12. UI/API Architecture

Expose a versioned API for:
- markets/instruments;
- live state;
- signals;
- strategies;
- orders;
- fills;
- positions;
- portfolio;
- risk;
- backtests;
- audit events;
- kill switch.

UI pages:
- Dashboard
- Market Scanner
- Signal Drilldown
- Strategy Monitor
- Portfolio
- Orders
- Fills
- Risk
- Backtests
- Audit Log
- System Health
- Emergency Controls

Manual trade execution must require explicit authorization and must still pass all risk controls.

## 13. Deployment Model

Start as a local modular application with scripts such as:

```
./scripts/run-market-data
./scripts/run-sim
./scripts/run-backtest
./scripts/run-mock-broker
./scripts/run-api
./scripts/run-ui
```

Package services into containers so the same images can later run in AWS/Azure.

Initial deployment may use Docker Compose. Later deployment can map components to managed container services, managed databases, queues/event buses and secret managers without changing domain logic.

## 14. Persistence

Separate:
- operational state;
- historical market data;
- event/audit history;
- backtest artifacts;
- configuration.

Use repository interfaces so local SQLite/files can later be replaced by managed PostgreSQL/object storage/time-series storage.

## 15. Observability

Every service must provide:
- structured JSON logs;
- correlation IDs;
- health/readiness;
- metrics;
- latency/error counters;
- decision/rejection counts;
- order/fill reconciliation status.

Never log secrets or credentials.

## 16. Security

- Secrets only through environment/secret manager.
- No broker credentials in source control.
- Least-privilege credentials.
- Separate market-data and trading permissions where possible.
- Live trading disabled by default.
- Explicit environment/mode declaration.
- Kill switch independent of strategy.

## 17. Testing Strategy

Required layers:
- unit tests for analyzers;
- property/invariant tests for risk;
- strategy tests;
- contract tests for broker adapters;
- deterministic backtest tests;
- mock execution tests;
- integration tests;
- failure/recovery tests;
- end-to-end simulation tests.

Critical invariants:
- risk cannot be bypassed;
- killed system cannot submit new orders;
- duplicate order cannot be created by retries;
- portfolio state reconciles with fills;
- no future data enters a decision;
- live and backtest strategy logic remain equivalent.

## 18. Architecture Rule

The system should be **modular monolith first, event-driven internally, container-ready, and service-splittable later**.

Do not prematurely distribute everything into microservices. Maintain strong domain boundaries and replaceable adapters so components can be separated only when operational scale requires it.
