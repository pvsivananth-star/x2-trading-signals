# X2 Trading System — Action TODO

Source of truth: `requirements.md` and `architecture.md`.

## Completed baseline

Phases 1–8 are implemented. The current branch is **compilable and test-clean**:
- `python -m unittest discover -s tests -v` → 177 tests passed, 3 skipped
- `python -m app.cli --help` → working
- Live broker routing remains disabled

## Phase 9 — Backtest correctness and dataset validation
- [x] 59. Reconcile `backtest-dataset` CLI options with the documented backtest workflow, including warmup/session options.
- [x] 60. Add preflight dataset validator for OHLCV integrity, timestamp ordering, duplicates and missing candles.
- [x] 61. Validate timeframe/session/calendar consistency for every input dataset.
- [x] 62. Validate instrument metadata, symbol eligibility, liquidity and tradability before replay.
- [x] 63. Detect corporate-action/split-adjustment inconsistencies where applicable.
- [x] 64. Add explicit look-ahead/leakage checks across all features, signals and strategy decisions.
- [x] 65. Validate synthetic warmup boundaries and ensure warmup data cannot contribute to scored performance.
- [x] 66. Produce a machine-readable dataset validation report and fail closed on critical violations.
- [x] 67. Add deterministic end-to-end backtest smoke fixtures and verify repeatability.

## Phase 10 — Strategy implementation and profitability validation
- [ ] 68. Replace first-pass analyzer/strategy heuristics with production-quality implementations backed by explicit requirements.
- [ ] 69. Validate Weekly Trend Following independently.
- [ ] 70. Validate Weekly Breakout/Pullback independently.
- [ ] 71. Validate MTF Trend Pullback Continuation independently.
- [ ] 72. Validate VWAP/Relative-Strength Continuation independently.
- [ ] 73. Validate Opening-Range Breakout with Trend Filter independently.
- [ ] 74. Add setup/trigger quality scoring and calibrated NO_TRADE thresholds.
- [ ] 75. Evaluate each strategy by symbol, regime, timeframe, setup, score and session.
- [ ] 76. Add walk-forward train/test evaluation with strict temporal separation.
- [ ] 77. Add out-of-sample acceptance gates: minimum trades, positive expectancy, PF > 1, controlled drawdown and stability across periods.
- [ ] 78. Add fee/slippage stress tests at normal, elevated and severe assumptions.
- [ ] 79. Add latency and execution-quality sensitivity analysis.
- [ ] 80. Add Monte Carlo/bootstrap trade-order stress analysis where statistically appropriate.
- [ ] 81. Reject strategies that only work in-sample or depend on fragile parameter values.
- [ ] 82. Produce a reproducible profitability-validation report with full lineage.

## Phase 11 — Portfolio optimization and account-level validation
- [ ] 83. Validate Long-Term portfolio independently from Daily Trading portfolio.
- [ ] 84. Validate capital allocation and risk budgets across strategies.
- [ ] 85. Validate portfolio-level gross/net exposure and concentration limits under concurrent signals.
- [ ] 86. Validate reservation/accounting behavior with concurrent orders and partial fills.
- [ ] 87. Validate strategy correlation and overlapping-position risk.
- [ ] 88. Evaluate combined-account drawdown, daily loss distribution and recovery behavior.
- [ ] 89. Verify that Long-Term positions are not incorrectly liquidated by Daily Trading signals.
- [ ] 90. Verify Daily Trading flattening/session policies under exchange calendar edge cases.
- [ ] 91. Define conservative account-level acceptance criteria; never force trades to meet a daily profit target.

## Phase 12 — Reliability and recovery hardening
- [ ] 92. Add integration tests covering Market Data → Signal → Strategy → Trade → Portfolio → Risk → Execution → Fill → Position.
- [ ] 93. Add restart/recovery tests for every stateful component.
- [ ] 94. Add duplicate, delayed and out-of-order event tests across the complete pipeline.
- [ ] 95. Add broker disconnect/reconnect/reconciliation integration scenarios.
- [ ] 96. Add database failure/recovery tests and verify fail-closed behavior.
- [ ] 97. Verify audit lineage survives retries, restarts and reconciliation.
- [ ] 98. Add concurrency tests proving strategy isolation and portfolio isolation.
- [ ] 99. Add kill-switch tests at strategy, portfolio, account and execution boundaries.

## Phase 13 — Persistence and API productionization
- [ ] 100. Replace in-memory operational state used by production paths with durable persistence.
- [ ] 101. Implement durable event/audit storage with idempotent writes.
- [ ] 102. Implement durable order/fill/position reconciliation state.
- [ ] 103. Add API authentication/authorization for non-public operational endpoints.
- [ ] 104. Add API request idempotency and operator audit logging.
- [ ] 105. Add pagination/filtering/sorting for operational read models.
- [ ] 106. Add dashboard drill-down from signal → decision → risk → order → fill → position.

## Phase 14 — Deployment and operational readiness
- [ ] 107. Run Docker build and container smoke tests.
- [ ] 108. Run backup/restore drill against a disposable PostgreSQL instance.
- [ ] 109. Run migration upgrade/rollback/recovery tests.
- [ ] 110. Add resource limits, graceful shutdown and startup/recovery checks.
- [ ] 111. Add production alerting for stale data, broker health, reconciliation failures, risk-off and kill switch.
- [ ] 112. Add operational runbooks for startup, shutdown, reconciliation, incident response and recovery.
- [ ] 113. Validate AWS deployment configuration in a non-production environment.
- [ ] 114. Verify secrets, IAM/network boundaries and database access using least privilege.

## Phase 15 — Paper trading readiness
- [ ] 115. Enable paper trading only after all Phase 9–14 acceptance gates pass.
- [ ] 116. Run paper trading with live market data but simulated/no-live-risk execution.
- [ ] 117. Compare paper fills against expected backtest execution assumptions.
- [ ] 118. Monitor strategy drift, data quality, latency, slippage and reconciliation.
- [ ] 119. Define minimum paper-trading observation period and acceptance criteria.
- [ ] 120. Confirm emergency kill switch, flattening and recovery procedures in paper mode.

## Phase 16 — Live trading authorization gate
- [ ] 121. Perform final architecture/security/risk review.
- [ ] 122. Verify every live-order path passes Portfolio Controller and Risk Manager.
- [ ] 123. Verify no strategy, UI, API or connector can bypass execution controls.
- [ ] 124. Verify live broker credentials are external, scoped and disabled by default.
- [ ] 125. Require explicit operator authorization before enabling live routing.
- [ ] 126. Start with conservative capital/risk limits and explicit instrument allowlists.
- [ ] 127. Monitor live reconciliation, risk, fills and kill-switch state continuously.
- [ ] 128. Document and approve the live trading go/no-go decision.

## Non-negotiable completion gates

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
11. Dataset integrity and leakage checks pass before profitability results are accepted.
12. Strategy acceptance is based on out-of-sample/walk-forward evidence, not in-sample performance alone.
13. Portfolio/account risk limits remain valid under concurrent strategies.
14. Recovery and reconciliation are tested, not merely documented.
15. No daily profit target may force a trade or increase risk.

**Current work: Phase 10 — strategy implementation and profitability validation.**
