# X2 Trading System — Action TODO

Source of truth: `requirements.md` and `architecture.md`.

## Phase 1 — Domain foundation
- [x] 1. Create canonical domain package boundaries.
- [x] 2. Model Long-Term and Daily Trading portfolios with isolated capital/risk contexts.
- [x] 3. Define immutable domain entities and stable reason codes.
- [x] 4. Define canonical event envelope: event_id, timestamp, correlation_id, causation_id, version, provenance.
- [x] 5. Define ports for market data, execution, account, history, clock, calendar, event bus, event store and notifications.
- [x] 6. Define runtime modes: backtest, paper/demo and live-disabled.
- [x] 7. Add clock/calendar abstractions and causal replay semantics.
- [x] 8. Add domain invariants and unit/property tests.

## Phase 2 — Market data and analysis
- [x] 9. Put existing feed/feature code behind MarketDataPort.
- [x] 10. Implement Instrument/Universe Actor and liquidity/quality gates.
- [x] 11. Implement Regime Actor.
- [x] 12. Implement Trend Analyzer for 1W/1D and 30m/15m/5m.
- [x] 13. Implement Volume/RVOL Analyzer.
- [x] 14. Implement Relative Strength Analyzer.
- [x] 15. Implement Structure/Fibonacci Analyzer.
- [x] 16. Implement DMA Analyzer.
- [x] 17. Implement Momentum Analyzer.
- [x] 18. Enforce causal cutoff and stale-data/feed-health gates.

## Phase 3 — Signal and strategy
- [ ] 19. Implement Signal Manager with SETUP versus TRIGGER.
- [ ] 20. Make signals immutable, explainable, deterministic and deduplicated.
- [ ] 21. Implement one isolated Strategy Manager per strategy.
- [ ] 22. Implement Strategy Registry / Portfolio Strategy Orchestrator.
- [ ] 23. Implement Long-Term: Weekly Trend Following.
- [ ] 24. Implement Long-Term: Weekly Breakout/Pullback.
- [ ] 25. Implement Daily: MTF Trend Pullback Continuation.
- [ ] 26. Implement Daily: VWAP/Relative-Strength Continuation.
- [ ] 27. Implement Daily: Opening-Range Breakout with Trend Filter.
- [ ] 28. Add explicit strategy-to-portfolio assignments and versioning.
- [ ] 29. Prevent strategy-to-strategy state mutation and risk-budget borrowing.
- [ ] 30. Keep NO_TRADE first-class; no single indicator may create a trade.

## Phase 4 — Trade, portfolio and risk
- [ ] 31. Implement Trade Manager / TradeIntent lifecycle.
- [ ] 32. Implement central Portfolio Controller.
- [ ] 33. Implement independent Risk Manager.
- [ ] 34. Enforce per-strategy, per-portfolio and account-wide limits.
- [ ] 35. Implement long-term trend-change/partial-liquidation policy.
- [ ] 36. Implement daily session/flattening policy.
- [ ] 37. Add circuit breakers and emergency kill switch invariants.

## Phase 5 — Execution
- [ ] 38. Implement Execution Manager and order state machine.
- [ ] 39. Add idempotency and duplicate-order protection.
- [ ] 40. Add partial fills, cancel/replace, rejects and reconciliation.
- [ ] 41. Keep Mock Broker behind ExecutionPort.
- [ ] 42. Keep IBKR/Binance adapters isolated and live-disabled.
- [ ] 43. Test disconnect/retry/recovery.

## Phase 6 — Backtest and paper
- [ ] 44. Route replay through the same strategy/portfolio/risk pipeline.
- [ ] 45. Model fees, spread, slippage, latency, partial fills and rejects.
- [ ] 46. Add walk-forward and out-of-sample evaluation.
- [ ] 47. Report performance by portfolio, strategy, symbol, regime, setup and score.
- [ ] 48. Add deterministic regression fixtures.

## Phase 7 — Persistence, API and UI
- [ ] 49. Separate operational state, market history, audit events and backtest artifacts.
- [ ] 50. Persist complete signal → risk → trade → order → fill → position lineage.
- [ ] 51. Define versioned API/read models.
- [ ] 52. Build dashboard, signal drill-down, strategy monitor, portfolio, orders, fills, risk and audit views.
- [ ] 53. Keep manual execution behind explicit authorization and risk controls.

## Phase 8 — Observability, security and deployment
- [ ] 54. Add structured logs, correlation IDs, health/readiness and metrics.
- [ ] 55. Enforce external secrets and least privilege.
- [ ] 56. Containerize the validated modular monolith.
- [ ] 57. Add AWS deployment configuration without cloud logic in the domain.
- [ ] 58. Add backup, restore, retention and migration procedures.

## Completion gates
1. No future data enters a decision.
2. Risk cannot be bypassed.
3. Kill switch prevents new orders.
4. Retries cannot duplicate orders.
5. Portfolio state reconciles from fills.
6. Backtest/paper/live-disabled paths use the same strategy/risk logic.
7. Every decision is explainable and auditable.
8. Live routing remains disabled until explicitly authorized.
9. Same inputs/config/code produce deterministic backtests.
10. NO_TRADE is preferred over a low-quality trade.

**Current work: Phase 3 (#19 onward). Strategy thresholds remain subordinate to portfolio/risk controls.**
