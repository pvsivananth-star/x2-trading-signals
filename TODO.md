# Implementation TODO

This is the execution checklist for the local-first trading platform. Work top-to-bottom unless a task is explicitly marked parallel. Keep the initial slice to one public venue and `BTCUSDT`; do not enable live trading as part of these tasks.

Legend: `[ ]` not started · `[~]` in progress · `[x]` complete · `[!]` blocked/decision required

## Phase 0 — Safety and project bootstrap

- [ ] Confirm the initial public provider's current API/dump terms, endpoints, retention, and redistribution restrictions.
- [ ] Record the provider decision and symbol mapping in source configuration; keep the provider behind an adapter interface.
- [ ] Choose and document the Python runtime/dependency policy.
- [ ] Add package metadata, pinned dependencies, formatting/linting, type-checking, and test commands.
- [ ] Add `.env.example` with non-secret local settings only; keep credentials out of Git.
- [ ] Add data-directory and credential patterns to `.gitignore`.
- [ ] Add a configuration validator that rejects live broker mode in the initial local implementation.
- [ ] Add a CI check for formatting, types, unit tests, migration checks, and secret/file hygiene.

**Exit criteria:** a clean checkout can install dependencies and run an empty application/test command without broker credentials.

## Phase 1 — Local infrastructure and database foundation

- [ ] Add local Docker Compose for PostgreSQL and application workers.
- [ ] Add PostgreSQL health checks and internal-only database networking.
- [ ] Add an applied-migrations table and ordered SQL migration runner.
- [ ] Apply and integration-test the existing `market_data` migration against PostgreSQL 15+.
- [ ] Add local partition creation/maintenance for the initial tick range.
- [ ] Enforce that only the ingestion worker can atomically transition a raw object from `discovered` to `verified` to `imported`; require a valid non-null checksum and preserved source bytes at verification, and record a failed state otherwise.
- [ ] Add `trading` tables for signals, decision cards, orders, fills, positions, cash, fees, and P&L.
- [ ] Add `backtest` tables for run metadata, provenance, fills, equity points, metrics, and quality summaries.
- [ ] Add seed data for one venue, one instrument, and one public ingestion source.
- [ ] Add database reset and fixture-loading commands for local development.

**Exit criteria:** `docker compose up` creates a usable local database, migrations are repeatable, and seed metadata is queryable.

## Phase 2 — Canonical market-data contracts

- [ ] Define typed raw trade/quote envelopes.
- [ ] Define normalized trade/quote contracts with UTC event time, receipt time, instrument ID, venue ID, provenance, and dedupe key.
- [ ] Define ingestion run, raw object, checkpoint, and quality-event contracts.
- [ ] Define provider-neutral interfaces for historical dumps, REST metadata, and WebSocket streams.
- [ ] Define decimal/timestamp serialization rules and reject floating-point prices/quantities at persistence boundaries.
- [ ] Add unit tests for serialization, timezone conversion, malformed input, and deterministic dedupe-key generation.

**Exit criteria:** provider payloads can be converted into validated domain objects without importing provider code into strategy/backtest modules.

## Phase 3 — Historical tick dump importer

- [ ] Implement the public-provider dump adapter for `BTCUSDT`.
- [ ] Accept a local file path or URL as a CLI argument; do not scan/import executable code from data directories.
- [ ] Preserve the original downloaded file under `data/raw/` and calculate a checksum before processing.
- [ ] Register the raw object and ingestion run before processing rows.
- [ ] Stream compressed/uncompressed rows without loading the full dump into memory.
- [ ] Normalize rows and write partitioned Parquet under `data/normalized/`.
- [ ] Upsert normalized ticks into PostgreSQL using idempotent deduplication.
- [ ] Persist accepted, duplicate, invalid, out-of-order, and gap counts.
- [ ] Make rerunning the same dump safe and produce the same counts/results.
- [ ] Add fixture-based tests for valid rows, malformed rows, duplicate rows, out-of-order rows, and partial failures.

**Exit criteria:** one historical dump can be imported twice with no duplicate logical ticks and complete provenance.

## Phase 4 — Live public WebSocket collector

- [ ] Implement the public trade-stream adapter for `BTCUSDT`.
- [ ] Add reconnect with bounded exponential backoff and clear terminal failure states.
- [ ] Persist checkpoints frequently enough to resume after process restart.
- [ ] Record event time and collector receipt time separately.
- [ ] Make writes idempotent across reconnect/replay windows.
- [ ] Expose collector health: connection state, last event, last sequence/event ID, error count, and lag.
- [ ] Mark the feed stale after a configurable threshold and block new signals/demo orders.
- [ ] Add a graceful shutdown path and restart integration test.

**Exit criteria:** stopping and restarting the collector does not create duplicate ticks, and stale-feed protection is observable and enforced.

## Phase 5 — Quality, bars, and features

- [ ] Implement required-field, positive-value, timestamp, symbol-mapping, ordering, and duplicate checks.
- [ ] Implement configurable sequence/timestamp gap detection.
- [ ] Persist quality events linked to source runs and affected time ranges.
- [ ] Build deterministic 1-minute base bars and 5-minute, 15-minute, and 30-minute rollups from canonical trades.
- [ ] Define bar close/finality semantics so live and replay modes agree across all configured timeframes.
- [ ] Implement exchange/session calendar handling without changing timestamps to local time at rest.
- [ ] Implement causal VWAP, ATR, RVOL, swing, Fibonacci-zone, trendline/structure, and net MoveRoom features required by `Confluence_v1`.
- [ ] Add tests proving a feature at time `t` cannot read data after `t`.
- [ ] Add fixture tests for gaps, empty intervals, partial bars, and restart/replay boundaries.

**Exit criteria:** a fixed tick fixture produces identical bars/features in batch replay and incremental processing.

## Phase 6 — Regime, policy, and strategy signals

- [ ] Implement the regime interface for `NEWS_DAY`, `CHOP_DAY`, and `TREND_DAY`.
- [ ] Implement a versioned policy layer that enforces configured risk/session/market gates in one place.
- [ ] Keep probationary and disabled markets blocked according to the existing configuration.
- [ ] Implement `Confluence_v1` using the configured timeframes and thresholds.
- [ ] Produce explainable signal decision cards containing inputs, rejected gates, selected zone, expected MoveRoom, and configuration version.
- [ ] Add deterministic signal IDs and deduplication.
- [ ] Add unit tests for every entry/exit gate and negative case.

**Exit criteria:** a fixture generates expected signals with an explanation, and no signal bypasses regime, stale-data, session, probation, or disabled-market gates.

## Phase 7 — Deterministic replay and backtesting

- [ ] Implement an event replay clock over normalized Parquet/PostgreSQL data.
- [ ] Reuse the production bar, feature, regime, and strategy path; only the event source differs.
- [ ] Implement an order simulator with market/limit behavior needed by the first strategy.
- [ ] Implement configurable spread, slippage, fees, and partial/ rejected fill behavior.
- [ ] Define gap policies and fail or annotate runs when quality thresholds are exceeded.
- [ ] Add chronological train/validation/test or walk-forward split support.
- [ ] Persist run provenance, configuration/code hashes, data-quality summary, fills, equity curve, and metrics.
- [ ] Calculate profit factor, drawdown, win rate, expectancy, streaks, exposure, and turnover.
- [ ] Add a deterministic golden backtest fixture and regression test.

**Exit criteria:** the same input, configuration, and code version produce the same backtest result and a reproducible audit trail.

## Phase 8 — Demo execution and accounting

- [ ] Implement a demo broker adapter that consumes signals but never contacts a real broker.
- [ ] Add explicit environment checks preventing live routing in local mode.
- [ ] Implement order lifecycle states and immutable order-state transitions.
- [ ] Implement fills, positions, cash, fees, realized P&L, unrealized P&L, and equity snapshots.
- [ ] Reconcile position/P&L calculations from fills in tests.
- [ ] Record signal-to-order-to-fill lineage and decision/audit cards.
- [ ] Enforce pre-close cutoff, mandatory flatten, session stops, and correlation caps from configuration; use an explicit rolling-session policy for 24/7 crypto instead of fixed exchange-close arithmetic.
- [ ] Keep probationary/zero-allocation crypto blocked in production policy; test demo-fill accounting with a non-probationary fixture or an explicitly approved policy change.

**Exit criteria:** a paper run produces internally consistent orders, fills, positions, cash, and P&L with no live network route, and no safety gate is bypassed to create a fill.

## Phase 9 — Read API and dashboard foundation

- [ ] Define versioned read-only API contracts for health, ingestion, quality, bars, signals, orders, fills, positions, P&L, and backtests.
- [ ] Implement API pagination and UTC timestamp serialization.
- [ ] Add authentication/authorization before exposing the API outside localhost.
- [ ] Add a dashboard for collector freshness, data quality, signals, working demo orders, trades, positions, and P&L.
- [ ] Keep write/control endpoints separate and disabled by default.
- [ ] Add API contract and authorization tests.

**Exit criteria:** the dashboard can display a paper run from persisted data without reading the database directly or handling secrets.

## Phase 10 — Cloud preparation (after local acceptance)

- [ ] Containerize the validated workers/API without cloud-specific domain logic.
- [ ] Replace local filesystem paths with an object-storage interface while retaining a local implementation.
- [ ] Define managed PostgreSQL/object-storage deployment manifests and secret injection.
- [ ] Add retention, backup, restore, and migration-run procedures.
- [ ] Add metrics, logs, alerts, and feed/data-quality monitoring.
- [ ] Threat-model the hosted API and dashboard.
- [ ] Require an explicit, separately reviewed authorization before implementing any live broker route.

**Exit criteria:** the local acceptance target passes in CI and the hosted deployment can be configured without changing strategy/backtest behavior.

## Decisions still required before implementation

- [ ] Confirm the public provider and acceptable data-license/terms reference.
- [ ] Define explicit crypto rolling-session fields (`rolling_session_window_mins`, `rolling_session_anchor_utc`, `rolling_entry_cutoff_mins`, and `rolling_flatten_mins`) and precedence over regular-session cutoffs before any crypto strategy evaluation; require a valid UTC anchor, `window > 0`, `0 <= flatten <= entry <= window`, and fail closed when fields are missing or invalid.
- [ ] Define the `BTC-USDT` canonical instrument and require a configured source-priority list, USD-per-USDT quote direction, positive rate, maximum age, and latest-at-or-before-event selection for USDT/USD valuation; record conversion provenance before USD sizing, fees, P&L, or demo fills, and fail closed rather than aliasing to `BTC-USD`.
- [ ] Define and validate tier resolution order: symbol override, explicit disabled/excluded state, explicit probation, market profile, then broad tier defaults; fail closed on conflicts such as the duplicate crypto tier listing.
- [ ] Confirm Python 3.12 as the implementation runtime.
- [ ] Confirm the first instrument (`BTCUSDT`) and historical date range.
- [ ] Confirm whether local PostgreSQL should run in Docker or use an existing installation.
- [ ] Confirm the desired first UI milestone: API-only first or API plus a minimal dashboard after paper execution.

## First coding milestone

Start with **Phases 0–3 only**. Do not begin strategy tuning until the historical importer can be run twice, produce identical results, and pass the deduplication/provenance tests. This prevents strategy results from being built on unreproducible or silently corrupted market data.
