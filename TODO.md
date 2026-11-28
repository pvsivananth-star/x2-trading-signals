# X2 Trading System — Implementation Roadmap

This checklist follows `requirements.md` and `architecture.md`. Those files are the source of truth; this file tracks implementation work only.

Legend: [ ] not started · [~] in progress · [x] complete

## 0. Repository cleanup and baseline

- [x] Keep `requirements.md` as the functional specification.
- [x] Keep `architecture.md` as the target architecture.
- [x] Identify stale planning/status documents that contradict the current repository.
- [x] Update README and roadmap to describe the actual baseline.
- [ ] Consolidate or remove obsolete planning material under `docs/`.
- [ ] Remove dead/duplicate code only after tests establish behavior that must be preserved.
- [ ] Establish one canonical configuration path for strategy, markets, risk and runtime mode.

## 1. Domain foundation

- [ ] Create explicit domain packages for instruments, market data, regimes, signals, trades, portfolio, risk, orders, fills and audit events.
- [ ] Define immutable domain entities/value objects and reason codes.
- [ ] Define canonical event envelope with event ID, timestamp, correlation ID, causation ID, version and provenance.
- [ ] Define domain/application ports independent of vendor SDKs and persistence.
- [ ] Define explicit runtime modes: backtest, demo/paper and live-disabled.
- [ ] Add clock and calendar abstractions so replay and live processing use identical semantics.

## 2. Market-data and analysis actors

- [ ] Refactor existing feed/feature code behind MarketDataPort and event contracts.
- [ ] Implement Instrument/Universe Actor.
- [ ] Implement Regime Actor.
- [ ] Implement Trend Analyzer for 30m/15m/5m.
- [ ] Implement Volume/RVOL Analyzer.
- [ ] Implement Relative Strength Analyzer.
- [ ] Implement Structure/Fibonacci Analyzer.
- [ ] Implement DMA Analyzer.
- [ ] Implement Momentum Analyzer.
- [ ] Enforce causal cutoff semantics across every analyzer.
- [ ] Add feed-health and stale-data gates.

## 3. Signal and strategy pipeline

- [ ] Implement Signal Manager with SETUP versus TRIGGER separation.
- [ ] Preserve the existing confluence rules while moving them behind strategy interfaces.
- [ ] Make every candidate explainable with immutable inputs, thresholds and rejection reasons.
- [ ] Add deterministic signal IDs and deduplication.
- [ ] Ensure no single indicator can create a trade.
- [ ] Add empirical, configuration-driven tolerance handling for Fib/DMA proximity.
- [ ] Add deterministic quality scoring and validated thresholds.
- [ ] Make NO_TRADE a first-class result.

## 4. Trade, portfolio and risk control

- [ ] Implement Trade Manager and TradeIntent lifecycle.
- [ ] Implement central Portfolio Controller.
- [ ] Implement independent Risk Manager.
- [ ] Add pre-trade risk decision with entry, stop, target/move room, size, max loss, slippage and portfolio impact.
- [ ] Enforce position, concentration, correlation, liquidity, spread and daily-loss limits.
- [ ] Add circuit breakers for consecutive losses, volatility, slippage, data/API failure and kill switch.
- [ ] Prove strategy code cannot bypass risk controls with invariant tests.

## 5. Execution

- [ ] Implement Execution Manager and explicit order state machine.
- [ ] Add idempotency keys and duplicate-order protection.
- [ ] Add acknowledgement, rejection, partial-fill and cancel/replace handling.
- [ ] Implement reconciliation against broker/account state.
- [ ] Keep Mock Trade API behavior compatible with ExecutionPort.
- [ ] Keep IBKR/Binance adapters isolated and disabled until separately authorized.
- [ ] Test disconnect/retry/recovery behavior.

## 6. Backtest and paper execution

- [ ] Refactor current backtest runner to use the same domain/application pipeline as demo/live.
- [ ] Reuse the same strategy, portfolio and risk components in replay.
- [ ] Preserve causal event replay and deterministic results.
- [ ] Model commissions, spread, slippage, latency, partial fills and rejects.
- [ ] Add walk-forward and out-of-sample evaluation.
- [ ] Report profit factor, expectancy, win rate, drawdown, exposure, turnover and rejection reasons by market/regime/setup/score.
- [ ] Add regression fixtures for every critical safety invariant.

## 7. Persistence and audit

- [ ] Separate operational state, market history, audit/event history, backtest artifacts and configuration.
- [ ] Introduce repository interfaces and keep PostgreSQL/filesystem behind adapters.
- [ ] Persist signal -> risk decision -> trade intent -> order -> fill -> position lineage.
- [ ] Persist configuration and strategy versions used by every decision.
- [ ] Make significant events immutable/auditable where practical.

## 8. API and UI

- [ ] Define versioned API contracts from the domain/read models.
- [ ] Dashboard: system health, scanner, signals, drill-down, strategy state, portfolio, positions, orders, fills, risk and P&L.
- [ ] Backtest results and audit timeline.
- [ ] Manual execution only through explicit authorization and the same risk path.
- [ ] Emergency kill-switch controls isolated from normal strategy controls.
- [ ] UI must never hold database or broker credentials.

## 9. Observability and security

- [ ] Structured JSON logging and correlation IDs.
- [ ] Health/readiness endpoints.
- [ ] Metrics for decisions, rejections, latency, errors, orders, fills and reconciliation.
- [ ] Secret-manager/environment based credentials only.
- [ ] Least privilege and separate market-data/trading permissions where possible.
- [ ] Threat-model the API and hosted dashboard.

## 10. Container/AWS readiness

- [ ] Containerize validated runtime components without cloud-specific domain logic.
- [ ] Replace local persistence details with storage ports.
- [ ] Add managed PostgreSQL/object-storage deployment configuration.
- [ ] Add retention, backup, restore and migration procedures.
- [ ] Add queue/event-bus adapter only when operational scale requires it.
- [ ] Keep the modular monolith deployable as one dedicated container.

## Acceptance gates

A phase is not complete merely because code exists. It must have automated tests and preserve these invariants:

1. No future data enters a decision.
2. Risk cannot be bypassed.
3. Kill switch prevents new orders.
4. Retries cannot create duplicate orders.
5. Portfolio state reconciles from fills.
6. Backtest and demo use the same strategy/risk behavior.
7. Every decision is explainable and auditable.
8. Live routing remains disabled until explicitly authorized.
9. Deterministic inputs/configuration/code produce deterministic backtest results.
10. The system prefers NO_TRADE over a low-quality trade.

## Immediate next milestone

**Clean baseline -> domain contracts -> actor boundaries -> signal pipeline -> portfolio/risk -> execution -> unified backtest/paper path.**

Do not tune strategy thresholds or add more indicators while the architectural boundaries are still being established.
