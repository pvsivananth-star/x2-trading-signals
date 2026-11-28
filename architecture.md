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
Combines analyzer outputs into immutable, explainable **candidate signals**, not orders. It must distinguish SETUP from TRIGGER, attach the causal cutoff and feature snapshot/version, and deduplicate equivalent candidates.

### Feature/Decision Snapshot
Captures the exact analyzer inputs, derived features, market-data references and configuration versions used for a decision. Snapshots make decisions reproducible without storing an unbounded copy of all market data.

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
Maintains the authoritative portfolio projection: positions, cash, realized/unrealized P&L, reserved/available capital, exposure, correlation and portfolio state. Position changes are driven by fills/account events, not strategy intent.

### Portfolio Controller
Evaluates proposed trade intents against portfolio allocation, capital reservations, cross-strategy netting and account-wide constraints. It resolves conflicts between strategy outputs and produces an explicit portfolio decision before Risk Manager approval.

### Risk Manager
Provides independent pre-trade and ongoing risk approval. It evaluates strategy/trade, portfolio and account-wide constraints and can impose scoped risk-off states. Strategy code cannot override it.

### Reconciliation Manager
Continuously compares local order/fill/position/account projections with broker/exchange state and owns mismatch detection, recovery workflow and scoped trading suspension.

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

### Event Store / Audit Store
Persist all significant events and decision snapshots:
market snapshot references, signals, approvals/rejections, orders, fills, portfolio/risk decisions, configuration versions and kill-switch actions.


## 3A. Architectural Boundaries That Must Not Be Blurred

**Signal Manager vs Strategy Manager**
- Signal Manager creates normalized, explainable market/setup candidates from analyzer outputs.
- Each Strategy Manager consumes candidates and strategy context, then makes an independent strategy decision.
- Strategy Managers do not own shared analyzer state and do not directly create broker orders.
- A strategy decision is not an approved trade.

**Trade Manager vs Portfolio Controller**
- Trade Manager owns the lifecycle of a trade intent.
- Portfolio Controller owns portfolio/account exposure, capital reservation and conflict resolution.
- Neither component is allowed to submit broker orders.

**Portfolio Controller vs Portfolio State**
- Portfolio state is derived from authoritative fills/account events.
- Portfolio Controller evaluates proposed state transitions and enforces policy.
- In-memory calculations are projections, not the durable source of truth.

**Risk Manager vs Execution Manager**
- Risk Manager answers whether a proposed risk-taking action is permitted.
- Execution Manager answers how an approved order is safely submitted, tracked and reconciled.
- Execution Manager cannot downgrade or bypass a risk decision.

**Event Bus vs Event Store**
- Event Bus transports events for processing.
- Event Store durably records the audit/event history required for recovery and replay.
- Delivery on the bus may be at-least-once; consumers must therefore be idempotent.

## 3B. Capital Reservation and Exposure Netting

Before an approved order reaches Execution Manager, the Portfolio Controller creates a deterministic reservation for:
- capital/buying power;
- trade risk;
- gross/net exposure;
- correlated/sector exposure;
- portfolio allocation.

Reservations have lifecycle states and unique IDs. They are released on cancellation/rejection/expiry and converted to actual consumed state from fills.

When multiple strategies propose the same instrument, strategy attribution remains separate but economic exposure is consolidated before portfolio/account risk checks. Opposing recommendations are resolved by explicit policy rather than implicit last-writer-wins behavior.

## 3C. Deterministic Event Semantics

All domain events use a canonical envelope containing at minimum:
- event_id;
- event_type;
- event_version;
- occurred_at;
- observed_at;
- causal_cutoff;
- correlation_id;
- causation_id;
- aggregate/entity ID;
- producer/component version;
- portfolio/strategy context where applicable;
- provenance/data-source reference.

Rules:
- event IDs are immutable and globally unique;
- consumers deduplicate by event ID;
- causally related events preserve ordering;
- timestamps are UTC internally;
- replay uses the original occurred_at/causal_cutoff, never wall-clock time;
- handlers are deterministic and idempotent;
- schema evolution is backward-compatible or explicitly version-migrated.

## 3D. State, Recovery and Reconciliation

Authoritative state is divided into:
1. durable business/audit events and persisted aggregates;
2. broker/exchange state after order submission;
3. derived caches/read models.

On startup or recovery:
- load durable state;
- reconcile broker/account state;
- rebuild derived projections;
- block new risk-increasing actions when reconciliation is incomplete.

If broker state and local state disagree, the system enters a scoped reconciliation/failsafe state rather than guessing.



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

## 5. Canonical Event Envelope and Event-Driven Contracts

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

Delivery semantics are explicitly **at-least-once**. Every consumer must be idempotent. The event bus is not the source of truth; durable event/audit storage is required for recovery and replay.

Event handlers must declare:
- input event types/versions;
- output events;
- idempotency key;
- ordering requirement;
- retry/dead-letter behavior;
- failure policy.

A poison/unprocessable event must not silently disappear. It must be quarantined and surfaced operationally.

## 5A. Command/Query Separation and Read Models

State-changing operations are explicit commands:
- create/cancel/replace order;
- approve/reject trade intent;
- allocate/release risk reservation;
- activate/release kill switch;
- transfer portfolio allocation.

Queries/read models must not mutate trading state. UI/API clients consume versioned read models and issue authorized commands.

## 5B. Configuration and Strategy Governance

Every strategy, risk policy, universe definition and execution policy has:
- immutable version;
- effective timestamp;
- validation status;
- provenance/owner;
- activation/deactivation audit record.

A backtest references exact strategy/config/data versions. Historical results are immutable even when a newer configuration is published.

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

## 8A. Failure and Recovery State Model

The platform must explicitly model system states such as:
- STARTING;
- HEALTHY;
- DEGRADED_DATA;
- DEGRADED_BROKER;
- RECONCILING;
- RISK_OFF;
- KILL_SWITCHED;
- READ_ONLY;
- STOPPED.

State transitions are auditable and scoped where possible (strategy, portfolio, broker or account). A degraded dependency may block new risk-taking without unnecessarily liquidating unrelated long-term positions.

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

## 11A. Market Session and Clock Architecture

All internal timestamps are UTC. Market/session logic uses a canonical exchange calendar and explicit timezone conversion.

The ClockPort supports:
- live monotonic/wall-clock time;
- deterministic replay time;
- clock-drift detection.

CalendarPort must represent holidays, early closes, halts, auction/opening-range windows and instrument-specific sessions.

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

## 13A. Data Lineage and Retention

Market-data lineage must identify provider, symbol mapping, venue, dataset/version and ingestion timestamp.

Persistence must support retention classes for:
- raw market data;
- normalized market data;
- feature/decision snapshots;
- operational state;
- audit events;
- backtest artifacts.

Backtest artifacts must be immutable and content-addressable or otherwise uniquely versioned.

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

## 16A. Operational Security and Authorization

Trading commands require authenticated, authorized principals. Authorization is separate from strategy logic.

At minimum distinguish:
- read-only;
- operator;
- trading operator;
- risk administrator;
- system administrator.

Emergency controls must remain available when normal strategy services are degraded. Kill-switch release must require explicit authorization and a healthy reconciliation state.

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

## 17A. Performance and Backpressure

The system must define bounded queues, event-processing latency budgets and overload behavior.

When the system cannot process market data within the configured freshness/latency budget:
- mark affected data stale;
- block new risk-increasing decisions;
- preserve existing state;
- recover/replay from durable sources.

No component may silently drop market or execution events.

## 18. Architecture Rule

The system should be **modular monolith first, event-driven internally, container-ready, and service-splittable later**.

Do not prematurely distribute everything into microservices. Maintain strong domain boundaries and replaceable adapters so components can be separated only when operational scale requires it.
