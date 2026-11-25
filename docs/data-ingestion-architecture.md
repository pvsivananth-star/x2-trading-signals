# Data Ingestion Architecture

## Purpose

This document defines the local-first market-data ingestion architecture for `x2-trading-signals`. It is designed to ingest historical tick dumps and live public market-data feeds, preserve the original data, normalize it into a common model, and make the same data available to backtests and paper/live execution.

The initial implementation should target one venue and one liquid instrument. Crypto is the recommended first market because public trade and quote feeds are generally easier to access than free, complete equity tick data.

## Design goals

- Preserve immutable source data and provenance.
- Use one normalized event model for historical and live data.
- Detect duplicates, gaps, invalid events, and out-of-order timestamps.
- Keep backtest and live feature calculations on the same code path.
- Keep broker/exchange-specific code behind adapters.
- Run locally with Docker Compose and migrate later to AWS or Azure without changing domain logic.
- Separate data licensing and source metadata from ingestion implementation.

## System context

```text
Historical tick dumps       Live public feed
        |                         |
        v                         v
+----------------+       +----------------+
| Dump importer  |       | WebSocket      |
| and validator  |       | collector      |
+----------------+       +----------------+
        |                         |
        +------------+------------+
                     v
             Normalization layer
                     |
          +----------+-----------+
          |                      |
          v                      v
   Data quality service    Immutable archive
          |                Parquet by venue/
          |                symbol/date
          v                      |
   PostgreSQL tick DB            |
   and metadata catalog          |
          |                      |
          +----------+-----------+
                     v
          Bar and feature builder
                     |
          +----------+-----------+
          |                      |
          v                      v
   Backtest/replay engine   Live signal engine
          |                      |
          +----------+-----------+
                     v
               API and dashboard
```

## Deployment modes

### Local mode

```text
Docker Compose
├── collector
├── normalizer
├── quality worker
├── bar/feature worker
├── API
├── PostgreSQL
├── DuckDB/Polars research jobs
└── Caddy or Nginx
```

The dashboard is served by the reverse proxy on the home LAN address. For example, a server at `192.168.1.50` is accessed from another home device at `http://192.168.1.50`.

Only the dashboard/API should be reachable from the LAN. PostgreSQL and worker ports should remain internal to Docker or the host.

### Cloud mode

The same containers can later be deployed as:

```text
AWS:    ECS/Fargate or EC2 + RDS PostgreSQL + S3
Azure:  Container Apps or VM + Azure Database for PostgreSQL + Blob Storage
```

The application should change only through environment variables, secret configuration, and deployment manifests. The core ingestion, normalization, quality, and strategy code must not import cloud-specific SDKs.

## Ingestion stages

### 1. Source registration

Every source is registered with:

- Provider and venue
- Source kind: `historical_dump`, `rest`, or `websocket`
- Endpoint or object location
- Supported event types
- Symbol mapping
- Time zone and timestamp rules
- License or permitted-use reference
- Checkpoint/resume behavior

An open-source client library is not the same as open market data. The source license and exchange terms must be recorded before storing or redistributing data.

### 2. Collection

Collectors write source events to a staging area. A collector must be restartable and must not silently discard an event.

For each collection run, record:

- Start/end time
- Requested event range
- Source object or stream
- SHA-256 checksum for every historical raw object; fail the object/run if it is unavailable or mismatched
- Raw record count
- Collector errors
- Last sequence or cursor

Historical dump files should be treated as immutable inputs. Live collectors should persist checkpoints frequently enough to resume after a process or network failure.

### 3. Normalization

Normalize all sources into common trade and quote records:

- UTC event time from the venue
- Local receipt time from the collector
- Internal instrument identifier
- Original venue symbol
- Venue trade or sequence identifier
- Decimal price and quantity
- Event type
- Source run/object reference

Do not overwrite the raw source. Normalization creates a new record with a link to the source run or object.

### 4. Data quality

The quality worker validates:

- Required fields and data types
- Positive price and quantity
- Valid bid/ask structure where available
- Duplicate trade IDs or sequence numbers
- Out-of-order events
- Sequence gaps
- Timestamp gaps beyond configured thresholds
- Symbol and instrument mapping
- Unexpected session or time-zone behavior

Quality events are stored rather than only logged. A backtest must be able to report which data-quality events affected its input range.

### 5. Storage

Use two storage layers:

#### Immutable archive

Store raw or normalized events as partitioned Parquet:

```text
data/raw/provider=binance/venue=binance/symbol=BTCUSDT/date=2026-01-01/sha256=<checksum>/source-object.zip
data/normalized/venue=binance/symbol=BTCUSDT/date=2026-01-01/*.parquet
```

`data/raw/` preserves the provider's native bytes and extension, together with its SHA-256 checksum. The content-addressed checksum component and no-overwrite rule prevent a corrected provider dump from replacing an earlier source object. `data/normalized/` contains derived canonical events in Parquet; it is never used as a replacement for the raw source object.

Parquet is the long-term archive and is portable to S3 or Azure Blob Storage.

#### Query and application database

PostgreSQL stores:

- Instrument and venue metadata
- Ingestion runs and source objects
- Quality events
- A hot/queryable window of trades and quotes
- Derived bars
- Backtest metadata and results
- Signals, orders, fills, and audit events

For the initial local implementation, all normalized ticks may also be loaded into PostgreSQL. When volume increases, keep the raw archive in Parquet and retain only a configurable hot window in PostgreSQL.

DuckDB and Polars should be used for large historical research scans without copying the entire archive into PostgreSQL.

## Event identity and deduplication

A source event should be considered a duplicate using the strongest available key in this order:

1. Venue plus venue event/trade ID
2. Venue plus instrument plus sequence number
3. Deterministic hash of normalized source payload

Deduplication must be idempotent. Replaying the same dump or reconnecting a collector must not create a second logical trade.

The normalizer must persist the selected identity as a deterministic `deduplication_key`; the database enforces uniqueness for an instrument, event time, and key. The final key construction remains source-specific because different venues provide different identifiers.

## Time model

- Store all timestamps as PostgreSQL `timestamptz` in UTC.
- Preserve venue event time and collector receipt time separately.
- Use exchange calendars for session boundaries and holidays.
- Do not infer a missing tick as a zero-price or zero-volume event.
- Record clock skew and timestamp corrections as quality events.

## Backtest handoff

The bar and feature builder must consume normalized events rather than vendor-specific payloads:

```text
trades/quotes
    -> event replay
    -> mandatory 1-minute base bars for the initial slice
    -> deterministic 5-minute, 15-minute, and 30-minute strategy bars
    -> indicators and features
    -> regime classifier
    -> signal engine
```

The live path must use the same bar and feature code as the replay path. Only the event source should differ.

Backtests must record:

- Data source/object IDs
- Instrument and date range
- Strategy version
- Configuration hash
- Fee and slippage model
- Data-quality summary
- Code version
- Result metrics

## Configuration boundaries

Keep source-specific values in configuration, not in Python code:

```env
DATA_MODE=historical|live|replay
BROKER_MODE=demo|live
DATABASE_URL=postgresql://...
ARCHIVE_ROOT=./data
HOT_TICK_RETENTION_DAYS=30
QUALITY_MAX_OUT_OF_ORDER_MS=1000
```

The UI must not control ingestion credentials or broker credentials. The API and worker processes own those secrets.

## Operational rules

- A failed quality check should mark the run `completed_with_errors` or `failed`; it must not appear successful.
- A collector must resume from a checkpoint after restart.
- A live stream must expose connection, last-event, and last-sequence health.
- A stale feed must block new signals.
- No live order should be submitted when the market-data health check is stale.
- Raw inputs should be retained long enough to reproduce a backtest.
- Data and broker credentials must never be committed to Git.

## Initial implementation scope

1. One public crypto venue.
2. One symbol, such as BTC/USDT.
3. Historical trade-dump importer.
4. Live public WebSocket trade collector.
5. Normalized Parquet archive.
6. PostgreSQL metadata and hot-tick tables.
7. Duplicate, ordering, and gap checks.
8. 1-minute base bar builder with deterministic 5-minute, 15-minute, and 30-minute rollups.
9. Replay interface shared by backtests and live processing.

Do not add multiple brokers, all global markets, or live trading until this path can reproduce a historical run and survive collector restart without duplicate data.
