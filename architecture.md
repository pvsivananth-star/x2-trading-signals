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
    +--> [Strategy Manager]
    |        +--> Strategy instances
    |        +--> Signal Manager
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

### Strategy Manager
Owns strategy lifecycle and configuration. A strategy decides whether a candidate signal represents a trade opportunity.

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

## 8. Risk Architecture

Risk checks occur independently at:
1. strategy/trade level;
2. portfolio level;
3. execution level;
4. broker/account reconciliation level.

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
