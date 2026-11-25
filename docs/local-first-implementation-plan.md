# Local-First Trading Platform Implementation Plan

## Status

This repository currently contains the trading-system specifications, market/strategy configuration, and the first PostgreSQL market-data migration. It does **not** yet contain executable collectors, workers, strategy code, a backtest engine, an API, or a dashboard.

This document turns the existing design into an implementation plan. The first implementation target is deliberately small:

- one public crypto venue;
- one venue symbol, initially `BTCUSDT`, represented as a distinct canonical `BTC-USDT` instrument until an explicit USDT/USD conversion policy exists;
- historical trades from the venue's public data dump;
- a live public WebSocket trade stream;
- immutable native raw source objects plus normalized local Parquet and a PostgreSQL hot/query store;
- 1-minute base bars with deterministic 5-minute, 15-minute, and 30-minute rollups;
- one deterministic strategy implementation whose policy gates remain enforced;
- reproducible backtests;
- demo/paper execution only where the configured market policy permits it.

Do not expand to all configured global markets, broker connections, or live order routing until this slice is restartable, reproducible, and covered by automated tests.

## Important terminology

An open-source client library is not the same thing as an open market-data source. The initial adapter should use a public exchange feed and record its provider, endpoint, symbol mapping, licensing/terms reference, and retrieval metadata. The client code remains replaceable.

The initial provider choice is an implementation default, not a permanent dependency:

- **Provider adapter:** Binance public market-data adapter, behind a provider-neutral interface.
- **Historical input:** Binance public historical trade dump where available, downloaded as an immutable source object.
- **Live input:** Binance public WebSocket trade stream.
- **Instrument:** venue symbol `BTCUSDT`, represented as canonical `BTC-USDT`; do not alias it to the configured USD instrument until an explicit USDT/USD conversion policy and provenance fields exist.

Before implementation, verify the provider's current endpoint, dump availability, retention, rate limits, exchange terms, and redistribution rights. No credentials are needed for public market data.

## Target architecture

```text
                         local Docker Compose

  public historical dump                         public WebSocket
          |                                            |
          v                                            v
  historical importer                         live collector
          |                                            |
          +-------------- immutable native raw --------+
                         source objects
                               |
                               v
                    normalizer + deduplicator
                       |                    |
                       v                    +--> data-quality events
          normalized Parquet archive
             (derived/reproducible)
                       |
                       +--> PostgreSQL hot/query store
                       |       - venue/instrument catalog
                       |       - ingestion runs/checkpoints
                       |       - normalized ticks
                       |       - bars and quality events
                       |       - signals/orders/fills/ledger
                       |
                       v
              shared bar + feature computation
                       |
                +------+------+
                |             |
                v             v
          replay/backtest   live signal engine
                |             |
                +------+------+
                       v
                 demo execution
                       |
                       v
              execution ledger and P&L
                       |
                       v
          FastAPI read API -> dashboard (later)
```

### Component boundaries

| Component | Responsibility | Must not do |
|---|---|---|
| `providers` | Convert provider-specific REST/WebSocket/dump formats into raw envelopes | Decide strategy signals or place orders |
| `ingestion` | Run collection, checkpoints, retries, provenance, and idempotent writes | Hide invalid data or overwrite raw input |
| `normalization` | Produce canonical UTC trade/quote records and deterministic dedupe keys | Infer missing ticks or silently repair source data |
| `quality` | Validate, classify, and persist invalid/duplicate/gap/order findings | Delete evidence of a quality failure |
| `bars` | Build time bars from canonical events | Use a separate implementation for backtests and live mode |
| `features` | Compute causal indicators/features from completed or explicitly forming bars | Read future bars or future ticks |
| `strategy` | Turn features and policy state into explainable signals | Submit broker orders |
| `backtest` | Replay the same event/bar/feature/strategy path with simulated fills | Mutate live state or call a broker |
| `execution` | Simulate demo orders and maintain order/fill/position state | Route live orders in the initial phases |
| `api` | Read health, data, signals, orders, fills, positions, and P&L | Accept credentials or expose broker secrets to the UI |
| `dashboard` | Display system state and reports | Control ingestion credentials or enable live routing |

## Local runtime

Recommended first implementation stack:

- Python 3.12 application and workers.
- PostgreSQL 15+ for metadata, hot ticks, bars, and application state.
- Native raw source objects and normalized Parquet under a configurable local `data/` directory. Only normalized events use Parquet; raw storage preserves provider bytes and native extensions.
- Polars/DuckDB for archive scans and research queries.
- Docker Compose for PostgreSQL and the application workers.
- Plain ordered SQL migrations initially, with an applied-migration table and a small migration runner.
- FastAPI only after the ingestion/backtest slice is usable; frontend work follows the API contract.

The code should be a regular Python package with one CLI entry point and separate worker commands. Provider SDKs must be optional adapters, not imported by strategy or backtest code.

Suggested layout:

```text
app/
  cli.py
  config.py
  domain/
    events.py             # raw and normalized trade/quote contracts
    instruments.py
    signals.py
    execution.py          # orders, fills, positions, ledger
  providers/
    base.py
    binance_public.py
  ingestion/
    dump_importer.py
    live_collector.py
    normalizer.py
    checkpoints.py
    writer.py
  quality/
    validators.py
    gap_detector.py
  marketdata/
    bars.py
    calendars.py
    repository.py
  features/
    vwap.py
    atr.py
    rvol.py
    swings.py
    fib.py
  strategy/
    base.py
    regime.py
    confluence_pullback.py
    policy.py
  backtest/
    replay.py
    fills.py
    costs.py
    runner.py
    metrics.py
  execution/
    demo_broker.py
    ledger.py
  api/
    main.py
    routes/
tests/
  unit/
  integration/
  fixtures/
migrations/
infra/
  docker-compose.local.yml
scripts/
```

The exact module names may change during implementation, but these boundaries should remain stable so the future dashboard and cloud deployment do not require a rewrite.

## Data lifecycle

### 1. Source registration

Add one enabled source record for the selected public venue and instrument. Capture provider, venue, source kind, endpoint/object pattern, supported events, symbol mapping, timezone rules, terms reference, and checkpoint strategy. Keep endpoint metadata free of credentials.

### 2. Historical dump import

The importer must:

1. accept a file path or URL as an argument;
2. calculate a checksum before processing;
3. register a `raw_objects` record;
4. stream rows without loading a full dump into memory;
5. write the untouched source file under `data/raw/`;
6. emit canonical normalized events and provenance;
7. upsert idempotently using the strongest available event identity;
8. record counts, invalid rows, duplicates, out-of-order rows, and failures;
9. allow only the ingestion worker to atomically transition a raw object from `discovered` to `verified` to `imported`; the `verified` transition must require an existing native copy, a valid SHA-256 value, and a matching checksum, while any mismatch records a failed object/run;
10. be safe to rerun against the same dump.

Downloaded files must be placed in a dedicated data directory and never committed to Git. The importer should accept paths as arguments rather than importing code from a downloaded-data directory.

### 3. Live collection

The WebSocket collector must reconnect with bounded backoff, persist a checkpoint, record receipt time separately from venue event time, expose last-event health, and shut down without losing acknowledged data. A stale or unhealthy feed must prevent new signals and demo orders.

### 4. Normalization and quality

Use one canonical event contract for historical and live paths:

- venue event timestamp in UTC;
- collector receipt timestamp in UTC;
- internal instrument ID;
- original venue symbol;
- venue trade/sequence ID when available;
- decimal price and quantity;
- event type;
- source run/object reference;
- deterministic deduplication key.

Persist quality findings. Never represent a missing tick as zero price or zero volume, and never silently rewrite the source record.

### 5. Storage

Use the existing `market_data` schema as the starting point. Extend it only when needed, with separate domains:

- `market_data.*` stores market observations (`trades` and `quotes` are market ticks, not our orders).
- `trading.*` stores strategy signals, orders, fills, positions, cash, fees, and realized/unrealized P&L.
- `backtest.*` stores run definitions, input provenance, configuration/code hashes, fills, metrics, and quality summaries.

Keep native raw source objects as the immutable audit source, and keep normalized Parquet as the reproducible analytical archive. PostgreSQL is the local query and application database, with a configurable hot-window policy once volume grows.

## Strategy and backtest architecture

The strategy path must be shared by replay and live processing:

```text
canonical events
  -> completed bars
  -> causal features
  -> regime classification
  -> policy/risk gate
  -> Confluence_v1 signal
  -> explainable decision card
```

The first strategy implementation should follow `config/strategies/confluence_pullback.yaml` and `strategies/confluence_pullback_continuation.md`, including causal VWAP/Fibonacci alignment, swing confirmation, RVOL, ATR, net MoveRoom after estimated costs, and confirmed trendline/structure exits. The bar layer must provide the configured 5-minute signal, 15-minute swing, and 30-minute discovery timeframes through deterministic rollups from the 1-minute base bars.

All existing configured risk and market gates must be represented in a single tested policy layer:

- `NEWS_DAY` blocks new entries.
- `CHOP_DAY` and `TREND_DAY` use the configured risk limits.
- `NVDA` remains probationary until its configured profit-factor gate is met.
- Crypto, Germany, and Sweden remain probationary.
- Tier resolution must be deterministic and tested in this order: symbol override status, explicit disabled/excluded lists and market `enabled`, explicit probation lists, market-profile tier/allocation, then broad tier lists as defaults. Any disabled status, probation status, or zero allocation blocks new entries. This makes the current duplicate `CRYPTO_MAJORS` tier listing resolve to probationary/blocked behavior rather than broad tier-B eligibility; conflicts fail closed.
- Hong Kong remains disabled.
- `SLV` remains excluded.
- For regular session markets, the configured pre-close cutoff and mandatory flatten rules remain unchanged.
- Before crypto strategy evaluation, add explicit `rolling_session_window_mins`, `rolling_session_anchor_utc`, `rolling_entry_cutoff_mins`, and `rolling_flatten_mins` fields to the market/session configuration. Require a valid UTC anchor, `window > 0`, and `0 <= rolling_flatten_mins <= rolling_entry_cutoff_mins <= window`; reject non-integer or negative values. For crypto, regular-session cutoffs are ignored; the effective window is `[anchor + floor((event_time - anchor) / window) * window, next window end)`, and entry/flatten cutoffs are measured backward from that deterministic window end. Missing or invalid rolling fields fail closed. Until these fields and precedence tests exist, never invent a fixed exchange close or apply regular-session close arithmetic to crypto.
- Initial execution remains demo/paper only, and crypto policy gates must not be bypassed to make a fixture trade.

Because the first data slice is crypto and `CRYPTO_MAJORS` is currently probationary with zero allocation, its initial acceptance path is ingestion, bars/features, replay, and explainable rejected decisions—not a production-configured crypto entry or demo fill. `BTC-USDT` must not be silently aliased to the configured USD instrument. Strategy calculations may be exercised as provider-neutral candidates, but the configured policy must reject the production signal until symbol mapping, valuation currency, and market eligibility are explicitly approved. Any future USD accounting path must require a timestamped USDT/USD valuation observation with a configured source-priority list, quote direction defined as USD per USDT, positive rate, and maximum age. Select the latest observation at or before the event time from the highest-priority available source, record its source/timestamp/rate as conversion provenance, and calculate `usd_value = usdt_value * usd_per_usdt`. Missing, future-dated, stale, invalid, or conflicting observations fail closed; never assume a 1:1 peg. The same conversion rule applies to sizing, fees, P&L, and demo fills. Demo-fill accounting must be tested separately with a non-probationary fixture or after an explicitly approved market-policy change; this plan does not change the existing configuration.

The backtest runner must record enough provenance to reproduce a result:

- source object/run IDs;
- venue, instrument, and UTC date range;
- strategy version;
- configuration hash;
- code commit/version;
- fee, spread, and slippage model;
- data-quality summary;
- random seed if any stochastic behavior is later introduced;
- orders, fills, equity curve, drawdown, profit factor, win rate, and streak metrics.

Use chronological train/validation/test or walk-forward splits. Never tune on the final out-of-sample period. A backtest must fail clearly when the requested range contains unacceptable data gaps unless the run explicitly declares a gap policy.

## API and future dashboard

The API is a later phase, but domain contracts should anticipate these read-only views:

- system health and collector freshness;
- ingestion runs, source objects, and quality events;
- bars and current market snapshot;
- signals with decision-card explanations;
- working demo orders and order state transitions;
- fills, positions, and cash;
- realized/unrealized P&L and equity curve;
- backtest runs, metrics, and data-quality summaries.

Write/control endpoints should be separate from read endpoints and disabled by default. The dashboard must not receive broker credentials. Authentication, authorization, audit logging, and a hard demo/live environment guard are mandatory before any hosted deployment or live execution.

## Local-first acceptance target

The first slice is complete only when all of the following are true:

1. A documented public historical dump for `BTCUSDT` can be imported locally.
2. Re-running the same import produces no duplicate logical ticks.
3. The original raw source object is preserved byte-for-byte with its checksum, and normalized events are stored as partitioned Parquet with provenance.
4. PostgreSQL contains the ingestion run, normalized ticks, and quality findings.
5. The collector can restart and resume without duplicate events.
6. The same bar/feature code runs in both replay and live modes.
7. A fixed fixture produces a deterministic backtest result.
8. Fees, spread, slippage, timestamps, and data gaps are visible in the result.
9. Strategy decisions, including crypto policy rejections, are explainable through persisted decision/audit data; demo fills are explainable in a separately approved non-probationary fixture.
10. Tests cover deduplication, restart, causality/look-ahead, timeframe rollups, risk gates, rolling crypto sessions, and P&L accounting.
11. No broker credentials, API keys, downloaded data, or live-order path are committed.

## Explicit non-goals for the first implementation

- Connecting IBKR or SCB.
- Enabling live order routing.
- Enabling HKEX or any probationary market.
- Implementing all 17+ markets before validating the first slice.
- Building a polished dashboard before the API and ledger contracts are stable.
- Treating an open-source library as a data license.
