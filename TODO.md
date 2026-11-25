# Implementation TODO

This is the execution checklist for the local-first trading platform. Work top-to-bottom unless a task is explicitly marked parallel. Test provider-neutral behavior with one fixture per category: EQ, ETF, Index, Gold, Oil, and Crypto. BTC-USD is the crypto fixture; Binance/BTCUSDT are optional crypto adapter examples, not system defaults. Do not enable live trading as part of these tasks.

Legend: `[ ]` not started · `[~]` in progress · `[x]` complete · `[!]` blocked/decision required

## Phase 0 — Safety and project bootstrap

- [~] Confirm public provider API/dump terms, endpoints, retention, and redistribution restrictions for providers used in each asset class. Keep adapters disabled until terms and attribution are verified.
- [x] Record the provider decision and symbol mapping in source configuration; keep the provider behind an adapter interface.
- [x] Choose and document the Python runtime/dependency policy.
- [x] Add package metadata, pinned dependencies, formatting/linting, type-checking, and test commands.
- [x] Add `.env.example` with non-secret local settings only; keep credentials out of Git.
- [x] Add data-directory and credential patterns to `.gitignore`.
- [x] Add a configuration validator that rejects live broker mode in the initial local implementation.
- [x] Add a CI check for formatting, compilation, unit tests, and secret/file hygiene.

**Exit criteria:** a clean checkout can install dependencies and run an empty application/test command without broker credentials.

## Phase 1 — Local infrastructure and database foundation

- [x] Add local Docker Compose for PostgreSQL and application workers.
- [x] Add PostgreSQL health checks and internal-only database networking.
- [x] Add an applied-migrations table and ordered SQL migration runner.
- [x] Apply and integration-test the `market_data` migrations against PostgreSQL 16 locally.
- [x] Add Docker startup, migration, test, shell, and reset scripts.
- [x] Add local partition creation/maintenance for the initial 2026-11 tick range.
- [x] Enforce that only the ingestion worker can atomically transition a raw object from `discovered` to `validated` to `imported`; require a valid non-null checksum and preserved source bytes at validation, and record a failed state otherwise.
- [ ] Add `trading` tables for signals, decision cards, orders, fills, positions, cash, fees, and P&L.
- [x] Add `backtest` tables for run metadata, provenance, fills, equity points, metrics, and quality summaries.
- [x] Add an initial seed catalog with a crypto venue/symbol example and provider-neutral EQ, ETF, index, metal, and future instrument support.
- [x] Add database reset commands for local Docker development.
- [x] Add fixture-loading and validation commands for local development.

**Exit criteria:** `docker compose up` creates a usable local database, migrations are repeatable, and seed metadata is queryable.

- [x] Add mock-broker Actor 02/04/06 runtime and migration 007 for demo quotes, trades, orders, fills, positions, and narrative audit cards.

## Phase 2 — Canonical market-data contracts

- [x] Define typed raw trade/quote envelopes with validation.
- [x] Define normalized trade/quote contracts with UTC event time, receipt time, instrument ID, venue ID, provenance, and dedupe key.
- [x] Define ingestion run, raw object, checkpoint, and quality-event contracts for the historical path.
- [x] Define provider-neutral interfaces for historical dumps and WebSocket streams; REST metadata remains.
- [x] Add a provider/source registry covering Binance, Yahoo, Google Finance, and Stooq metadata. License status remains unverified until reviewed.
- [x] Add EQ, ETF, Metal, FX, Crypto, futures, options, bond, and index asset-class support plus instrument aliases and RSS/news feed storage.
- [x] Define decimal/timestamp serialization rules and reject floating-point prices/quantities at persistence boundaries.
- [x] Add unit tests for serialization, timezone conversion, malformed input, and deterministic dedupe-key generation.

**Exit criteria:** provider payloads can be converted into validated domain objects without importing provider code into strategy/backtest modules.

## Phase 3 — Historical tick dump importer

- [x] Implement an optional Binance public crypto-dump adapter for `BTCUSDT` as an implementation example.
- [x] Accept a local file path or URL as a CLI argument; do not scan/import executable code from data directories.
- [x] Preserve the original downloaded file under `data/raw/` and calculate a checksum before processing.
- [x] Register the raw object and ingestion run before processing rows when PostgreSQL is configured.
- [x] Stream compressed/uncompressed rows without loading the full dump into memory.
- [x] Normalize rows and write partitioned Parquet under `data/normalized/` (runtime dependency is included in Docker; unit fixtures verify deterministic rollups).
- [x] Upsert normalized ticks into PostgreSQL using idempotent deduplication.
- [x] Persist accepted, duplicate, invalid, out-of-order, and gap counts.
- [x] Make rerunning the same dump safe and produce the same counts/results with the repository deduplication path.
- [x] Add fixture-based tests for valid rows, malformed rows, duplicate rows, out-of-order rows, and partial failures.

**Exit criteria:** one historical dump can be imported twice with no duplicate logical ticks and complete provenance.

## Phase 4 — Live public WebSocket collector

- [~] Implement a provider-neutral public trade-stream adapter contract and optional Binance/BTCUSDT implementation. Foundation and optional parser exist; network transport is not implemented.
- [x] Add reconnect with bounded exponential backoff and clear terminal failure states.
- [x] Persist checkpoints frequently enough to resume after process restart (restart-persistent in-memory store foundation; tested).
- [x] Record event time and collector receipt time separately.
- [x] Make writes idempotent across reconnect/replay windows.
- [x] Expose collector health: connection state, last event, last sequence/event ID, error count, and lag.
- [x] Mark the feed stale after a configurable threshold and block new signals/demo orders.
- [x] Add a graceful shutdown path and restart integration test.

**Exit criteria:** stopping and restarting the collector does not create duplicate ticks, and stale-feed protection is observable and enforced.

## Phase 5 — Quality, bars, and features

- [x] Implement required-field, positive-value, timestamp, symbol-mapping, ordering, and duplicate checks.
- [x] Implement configurable sequence/timestamp gap detection.
- [~] Persist quality events linked to source runs and affected time ranges. Finding model exists; database persistence remains pending.
- [x] Build deterministic 1-minute and 5/15-minute rollups from synthetic six-category bar fixtures.
- [x] Build the 30-minute rollup and production replay integration.
- [x] Define bar close/finality semantics so live and replay modes agree across all configured timeframes.
- [x] Implement exchange/session calendar handling without changing timestamps to local time at rest.
- [~] Implement causal VWAP, ATR, RVOL, swing, Fibonacci-zone, trendline/structure, and net MoveRoom features required by `Confluence_v1`. Causal and tested, but not yet spec-conformant: VWAP is not session-anchored, RVOL is not slot-normalized, swings lack the 15m/0.60×ATR/2-bar-confirmation rules, and MoveRoom does not net spread/slippage/fees (see Phase 12).
- [x] Add tests proving a feature at time `t` cannot read data after `t`.
- [x] Add fixture tests for gaps, empty intervals, partial bars, and restart/replay boundaries.

**Exit criteria:** a fixed tick fixture produces identical bars/features in batch replay and incremental processing.

## Phase 6 — Regime, policy, and strategy signals

- [x] Implement the regime interface for `NEWS_DAY`, `CHOP_DAY`, and `TREND_DAY`.
- [x] Implement a versioned policy layer that enforces configured risk/session/market gates in one place.
- [x] Keep probationary and disabled markets blocked according to the existing configuration.
- [~] Implement `Confluence_v1` using the configured timeframes and thresholds. Reaction zones, VWAP-hold/reclaim requirements, confirmed trend-break exits, and pre-close flatten now enforced (review v1). Swing, RVOL, VWAP, and MoveRoom conformance remains (Phase 12).
- [x] Produce explainable signal decision cards containing inputs, rejected gates, selected zone, expected MoveRoom, configuration version, size weight, and max-risk cap.
- [x] Add deterministic signal IDs and deduplication.
- [~] Add unit tests for every entry/exit gate and negative case. Zone boundaries, VWAP hold/reclaim, single-bar vs confirmed break, pre-close flatten, and missing-input fail-closed are covered. Not yet covered: regime-gated exits, daily-stop interaction, and sizing.

**Exit criteria:** a fixture generates expected signals with an explanation, and no signal bypasses regime, stale-data, session, probation, or disabled-market gates.

## Phase 7 — Deterministic replay and backtesting

- [x] Add deterministic 1/5/15-minute fixtures for EQ, ETF, Index, Gold, Oil, and BTC-USD, plus market-buy replay primitives.
- [x] Implement an event replay clock over normalized in-memory event feeds, reusable with canonical Bar/Trade replay abstractions.
- [~] Reuse the production bar and feature paths in replay. The tick pump now evaluates the strategy at bar time with previous-snapshot confirmation; the golden backtest fixture still needs regeneration against the review v1 rules (Phase 12).
- [x] Implement an order simulator with market/limit behavior needed by the first strategy.
- [x] Implement configurable spread, slippage, fees, and partial/rejected fill behavior.
- [x] Define gap policies and fail or annotate runs when quality thresholds are exceeded.
- [x] Add chronological train/validation/test and walk-forward split support.
- [x] Persist run provenance, configuration/code hashes, data-quality summary, fills, equity curve, and metrics in the data model and optional PostgreSQL repository.
- [x] Calculate profit factor, drawdown, win rate, expectancy, streaks, exposure, and turnover.
- [x] Add a deterministic golden backtest fixture and regression test.

**Exit criteria:** the same input, configuration, and code version produce the same backtest result and a reproducible audit trail.

## Phase 8 — Demo execution and accounting

- [x] Implement a demo broker adapter that consumes signals but never contacts a real broker.
- [x] Add explicit environment checks preventing live routing in local mode.
- [x] Implement order lifecycle states and immutable order-state transitions.
- [x] Implement fills, positions, cash, fees, realized P&L, unrealized P&L, and equity snapshots.
- [x] Reconcile position/P&L calculations from fills in tests.
- [x] Record signal-to-order-to-fill lineage and decision/audit cards.
- [~] Enforce pre-close cutoff, mandatory flatten, session stops, and correlation caps from configuration. Pre-close entry cutoff and mandatory flatten exit are enforced in the strategy. Session stops and correlation caps are not implemented (Phase 12). Crypto uses existing policy blocking; explicit rolling-session fields remain pending because no approved rolling-session policy exists.
- [x] Keep probationary/zero-allocation crypto blocked in production policy; demo accounting tests use the non-probationary US equities fixture without changing risk policy.

**Exit criteria:** a paper run produces internally consistent orders, fills, positions, cash, and P&L with no live network route, and no safety gate is bypassed to create a fill.

## Phase 9 — Read API and dashboard foundation

- [x] Define versioned read-only API contracts for health, ingestion, quality, bars, signals, orders, fills, positions, P&L, and backtests.
- [x] Implement API pagination and UTC timestamp serialization.
- [x] Add authentication/authorization before exposing the API outside localhost.
- [x] Add a minimal localhost read-only HTML dashboard foundation for API data; persisted PostgreSQL source wiring remains for future integrations.
- [x] Keep write/control endpoints separate and disabled by default.
- [x] Add API contract and authorization tests.

**Exit criteria:** the dashboard can display a paper run from persisted data without reading the database directly or handling secrets.

## Phase 10 — Configurable feeds and market dashboard

- [x] Add `config/feeds.yaml` for EQ, ETF, Index, Gold, Oil, and Crypto symbols with provider, license/status, refresh, timeframe, and enabled fields.
- [x] Implement provider-neutral candle, Yahoo Chart REST, Stooq CSV, Binance public klines, cache, stale, quality, and deterministic synthetic-fallback support.
- [x] Implement in-process regime/scanner/decision actors using existing policy, Confluence_v1, and causal features; synthetic/stale feeds are rejected and live orders remain disabled.
- [x] Add rotating file/console logging and `start-api --with-feeds`, `start-feed`, and `status` CLI integration.
- [x] Extend the read-only API and vanilla dashboard with market grouping, symbol/timeframe bars, candlesticks, 20/50/200 DMAs, Fibonacci zones, and OHLC/volume metadata.
- [ ] Verify public provider terms and redistribution/attribution status before changing the configured `unknown` provider status.

## Phase 11 — Cloud preparation (after local acceptance)

- [ ] Containerize the validated workers/API without cloud-specific domain logic.
- [ ] Replace local filesystem paths with an object-storage interface while retaining a local implementation.
- [ ] Define managed PostgreSQL/object-storage deployment manifests and secret injection.
- [ ] Add retention, backup, restore, and migration-run procedures.
- [ ] Add metrics, logs, alerts, and feed/data-quality monitoring.
- [ ] Threat-model the hosted API and dashboard.
- [ ] Require an explicit, separately reviewed authorization before implementing any live broker route.

**Exit criteria:** the local acceptance target passes in CI and the hosted deployment can be configured without changing strategy/backtest behavior.

## Phase 12 — Strategy spec conformance (from code review v1)

Items found by reviewing `Confluence_v1`, `actors/02`, `actors/04`, and `actors/06` against the code. Each needs a test before it is marked done.

- [ ] Swing detection: enforce 15-minute swing timeframe, minimum `3` candles, minimum size `>= 0.60 × ATR(14, 15m)`, and confirmation after `M = 2` closed 15-minute bars. Currently `causal_swing` has none of these.
- [ ] VWAP: reset causal VWAP per exchange session, not cumulative across all supplied bars.
- [ ] RVOL: implement 30-minute slot-normalized RVOL (time-of-day profile) per `actors/02` Stage 1 instead of last-bar-vs-prior-19-bars.
- [ ] MoveRoom: net of spread, slippage, and market-specific fees/taxes per `strategies/confluence_pullback_continuation.md`, instead of fib-618 target / fib-382 stop.
- [ ] Sizing: apply `size_weight` (1.00 core, 0.68 shallow/deep) and the `max_risk_pct` cap (CHOP 0.08%, TREND 0.25% A-tier / 0.17% B-tier) in execution. Cards emit the values; nothing consumes them yet.
- [ ] Exit rules: add target/runner rule and daily stop (soft/hard) per `actors/04`. Drawdown throttle is also pending.
- [ ] Session stops: daily soft and hard loss stops, and drawdown throttles from `actors/04`.
- [ ] Correlation caps: enforce per-cluster exposure limits across sleeves (`actors/04`).
- [ ] Actor 02 Stage 1: replace configured `slot_rvol` flags with computed discovery at 30-minute cadence, excluding first 15 minutes, lunch, and final 60 minutes, and excluding `NEWS_DAY` and gap-exhausted names.
- [ ] Actor 02 Stage 2: 5-minute streaming evaluation on promoted candidates only. Current scanner reaction input is a boolean passed in by the caller.
- [ ] Actor 06 probation gates: enforce `NVDA` PF > 1.25 and `HKEX` PF > 1.10 re-enable, `SLV` exclusion, and monthly/quarterly/out-of-sample metrics.
- [ ] Regime: confirm the `NEWS_DAY` classifier is causal and point-in-time; currently a regime provider stub.
- [ ] Golden regression: regenerate the golden backtest fixture after the review v1 rule changes and record the reason for each changed count.
- [ ] Naming: align the strategy ID. Code and YAML use `confluence_pullback_v1`; the strategy doc and README use `Confluence_v1`. Pick one and update the others.
- [ ] Repo governance: `AGENTS.md` on `main` allows agents to push feature branches automatically. This conflicts with the owner-pushes rule. Decide which applies and update `AGENTS.md` to match.

## Data (blocked by sandbox network)

- [ ] Bring in additional historical backtest data. Required before strategy tuning. Blocked: the build sandbox can reach only GitHub, PyPI, and Ubuntu mirrors, so Binance, Yahoo, and Stooq cannot be fetched there. Use a machine with network access and the verified provider terms in Phase 10.
- [ ] Optional: extend `scripts/build_five_day_bars_streaming.py` to longer synthetic ranges for stress tests only. Must stay labeled `synthetic` and cannot support performance claims.

## Decisions still required before implementation

- [ ] Confirm the public provider and acceptable data-license/terms reference.
- [ ] Define explicit crypto rolling-session fields (`rolling_session_window_mins`, `rolling_session_anchor_utc`, `rolling_entry_cutoff_mins`, and `rolling_flatten_mins`) and precedence over regular-session cutoffs before any crypto strategy evaluation; require a valid UTC anchor, `window > 0`, `0 <= flatten <= entry <= window`, and fail closed when fields are missing or invalid.
- [ ] Define the `BTC-USDT` canonical instrument and require a configured source-priority list, USD-per-USDT quote direction, positive rate, maximum age, and latest-at-or-before-event selection for USDT/USD valuation; record conversion provenance before USD sizing, fees, P&L, or demo fills, and fail closed rather than aliasing to `BTC-USD`.
- [ ] Define and validate tier resolution order: symbol override, explicit disabled/excluded state, explicit probation, market profile, then broad tier defaults; fail closed on conflicts such as the duplicate crypto tier listing.
- [ ] Confirm Python 3.12 as the implementation runtime.
- [ ] Confirm real-market data availability and historical date ranges for AAPL, SPY, SPX, GC=F, CL=F, and BTC-USD. Synthetic fixtures are testing-only and do not satisfy this item.
- [x] Confirm whether local PostgreSQL should run in Docker or use an existing installation (Docker Compose PostgreSQL 16 is the local default).
- [ ] Confirm the desired first UI milestone: API-only first or API plus a minimal dashboard after paper execution.

## First coding milestone

Start with **Phases 0–3 only**. Do not begin strategy tuning until the historical importer can be run twice, produce identical results, and pass the deduplication/provenance tests. This prevents strategy results from being built on unreproducible or silently corrupted market data.
