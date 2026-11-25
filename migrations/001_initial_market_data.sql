-- x2-trading-signals market-data schema
-- Target: PostgreSQL 15+
-- IDs are supplied by the application so this schema does not require extensions.
-- Raw source files should also be preserved as immutable Parquet objects.

BEGIN;

CREATE SCHEMA IF NOT EXISTS market_data;

-- Exchange, broker, or data-provider venue.
CREATE TABLE IF NOT EXISTS market_data.venues (
    venue_id            text PRIMARY KEY,
    display_name        text NOT NULL,
    asset_class         text NOT NULL CHECK (asset_class IN ('equity', 'etf', 'future', 'forex', 'crypto', 'option', 'other')),
    country_code        text,
    timezone_name       text NOT NULL,
    data_license_ref    text,
    enabled             boolean NOT NULL DEFAULT true,
    created_at          timestamptz NOT NULL DEFAULT now()
);

-- A venue-specific tradable instrument and its canonical application symbol.
CREATE TABLE IF NOT EXISTS market_data.instruments (
    instrument_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    venue_id            text NOT NULL REFERENCES market_data.venues (venue_id),
    venue_symbol        text NOT NULL,
    canonical_symbol    text NOT NULL,
    asset_class         text NOT NULL CHECK (asset_class IN ('equity', 'etf', 'future', 'forex', 'crypto', 'option', 'other')),
    base_currency       text,
    quote_currency      text,
    price_scale         smallint NOT NULL CHECK (price_scale >= 0),
    quantity_scale      smallint NOT NULL CHECK (quantity_scale >= 0),
    tick_size           numeric(38, 18) NOT NULL CHECK (tick_size > 0),
    lot_size            numeric(38, 18) NOT NULL CHECK (lot_size > 0),
    contract_multiplier numeric(38, 18) NOT NULL DEFAULT 1 CHECK (contract_multiplier > 0),
    valid_from          timestamptz,
    valid_to            timestamptz,
    enabled             boolean NOT NULL DEFAULT true,
    metadata            jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (venue_id, venue_symbol),
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from)
);

-- A source definition. The endpoint is metadata only; credentials belong in a secret store.
CREATE TABLE IF NOT EXISTS market_data.ingestion_sources (
    source_id           uuid PRIMARY KEY,
    source_name         text NOT NULL,
    source_kind         text NOT NULL CHECK (source_kind IN ('historical_dump', 'rest', 'websocket')),
    provider_name       text NOT NULL,
    venue_id            text REFERENCES market_data.venues (venue_id),
    endpoint            text,
    license_reference   text,
    enabled             boolean NOT NULL DEFAULT true,
    configuration       jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at          timestamptz NOT NULL DEFAULT now()
);

-- Immutable historical files or object-storage objects consumed by an import run.
CREATE TABLE IF NOT EXISTS market_data.raw_objects (
    raw_object_id       uuid PRIMARY KEY,
    source_id           uuid NOT NULL REFERENCES market_data.ingestion_sources (source_id),
    object_uri          text NOT NULL,
    checksum_sha256     text,
    byte_size           bigint CHECK (byte_size IS NULL OR byte_size >= 0),
    compression         text,
    first_event_time    timestamptz,
    last_event_time     timestamptz,
    record_count        bigint CHECK (record_count IS NULL OR record_count >= 0),
    status              text NOT NULL CHECK (status IN ('discovered', 'validated', 'imported', 'rejected')),
    metadata            jsonb NOT NULL DEFAULT '{}'::jsonb,
    discovered_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_id, object_uri)
);

-- One historical import, live session, or replay operation.
CREATE TABLE IF NOT EXISTS market_data.ingestion_runs (
    run_id              uuid PRIMARY KEY,
    source_id           uuid NOT NULL REFERENCES market_data.ingestion_sources (source_id),
    instrument_id       bigint REFERENCES market_data.instruments (instrument_id),
    raw_object_id       uuid REFERENCES market_data.raw_objects (raw_object_id),
    run_kind            text NOT NULL CHECK (run_kind IN ('backfill', 'live', 'replay', 'reprocess')),
    status              text NOT NULL CHECK (status IN ('created', 'running', 'completed', 'completed_with_errors', 'failed', 'cancelled')),
    requested_start     timestamptz,
    requested_end       timestamptz,
    started_at          timestamptz,
    finished_at         timestamptz,
    raw_record_count    bigint NOT NULL DEFAULT 0 CHECK (raw_record_count >= 0),
    accepted_count      bigint NOT NULL DEFAULT 0 CHECK (accepted_count >= 0),
    duplicate_count     bigint NOT NULL DEFAULT 0 CHECK (duplicate_count >= 0),
    invalid_count       bigint NOT NULL DEFAULT 0 CHECK (invalid_count >= 0),
    out_of_order_count  bigint NOT NULL DEFAULT 0 CHECK (out_of_order_count >= 0),
    gap_count           bigint NOT NULL DEFAULT 0 CHECK (gap_count >= 0),
    error_message       text,
    configuration       jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CHECK (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at),
    CHECK (requested_end IS NULL OR requested_start IS NULL OR requested_end > requested_start)
);

-- Resume state for live collectors. Update this transactionally with accepted events.
CREATE TABLE IF NOT EXISTS market_data.ingestion_checkpoints (
    source_id           uuid NOT NULL REFERENCES market_data.ingestion_sources (source_id),
    instrument_id       bigint NOT NULL REFERENCES market_data.instruments (instrument_id),
    stream_name         text NOT NULL,
    last_event_time     timestamptz,
    last_sequence_no    bigint,
    last_venue_event_id text,
    state               jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, instrument_id, stream_name)
);

-- Normalized trade ticks. This is a hot/queryable copy; the immutable archive is Parquet.
CREATE TABLE IF NOT EXISTS market_data.trades (
    trade_row_id        bigint GENERATED ALWAYS AS IDENTITY,
    instrument_id       bigint NOT NULL REFERENCES market_data.instruments (instrument_id),
    event_time          timestamptz NOT NULL,
    received_time       timestamptz NOT NULL,
    venue_trade_id      text,
    sequence_no         bigint,
    price               numeric(38, 18) NOT NULL CHECK (price > 0),
    quantity            numeric(38, 18) NOT NULL CHECK (quantity > 0),
    aggressor_side      text CHECK (aggressor_side IN ('buy', 'sell', 'unknown')),
    source_run_id       uuid NOT NULL REFERENCES market_data.ingestion_runs (run_id),
    payload_ref         text,
    PRIMARY KEY (event_time, trade_row_id)
) PARTITION BY RANGE (event_time);

-- Safety partition for data received before a managed time partition exists.
CREATE TABLE IF NOT EXISTS market_data.trades_default
    PARTITION OF market_data.trades DEFAULT;

CREATE INDEX IF NOT EXISTS trades_instrument_event_idx
    ON market_data.trades (instrument_id, event_time);
CREATE INDEX IF NOT EXISTS trades_venue_id_idx
    ON market_data.trades (instrument_id, venue_trade_id)
    WHERE venue_trade_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS trades_sequence_idx
    ON market_data.trades (instrument_id, sequence_no)
    WHERE sequence_no IS NOT NULL;

-- Normalized top-of-book quote ticks.
CREATE TABLE IF NOT EXISTS market_data.quotes (
    quote_row_id        bigint GENERATED ALWAYS AS IDENTITY,
    instrument_id       bigint NOT NULL REFERENCES market_data.instruments (instrument_id),
    event_time          timestamptz NOT NULL,
    received_time       timestamptz NOT NULL,
    sequence_no         bigint,
    bid_price           numeric(38, 18),
    bid_quantity        numeric(38, 18),
    ask_price           numeric(38, 18),
    ask_quantity        numeric(38, 18),
    source_run_id       uuid NOT NULL REFERENCES market_data.ingestion_runs (run_id),
    payload_ref         text,
    PRIMARY KEY (event_time, quote_row_id),
    CHECK (bid_price IS NOT NULL OR ask_price IS NOT NULL),
    CHECK (bid_price IS NULL OR bid_price > 0),
    CHECK (ask_price IS NULL OR ask_price > 0),
    CHECK (bid_quantity IS NULL OR bid_quantity >= 0),
    CHECK (ask_quantity IS NULL OR ask_quantity >= 0)
) PARTITION BY RANGE (event_time);

CREATE TABLE IF NOT EXISTS market_data.quotes_default
    PARTITION OF market_data.quotes DEFAULT;

CREATE INDEX IF NOT EXISTS quotes_instrument_event_idx
    ON market_data.quotes (instrument_id, event_time);
CREATE INDEX IF NOT EXISTS quotes_sequence_idx
    ON market_data.quotes (instrument_id, sequence_no)
    WHERE sequence_no IS NOT NULL;

-- Derived bars generated from normalized events. interval_seconds allows 1m, 5m, etc.
CREATE TABLE IF NOT EXISTS market_data.bars (
    instrument_id       bigint NOT NULL REFERENCES market_data.instruments (instrument_id),
    interval_seconds    integer NOT NULL CHECK (interval_seconds > 0),
    bar_start           timestamptz NOT NULL,
    bar_end             timestamptz NOT NULL,
    open_price          numeric(38, 18) NOT NULL CHECK (open_price > 0),
    high_price          numeric(38, 18) NOT NULL CHECK (high_price > 0),
    low_price           numeric(38, 18) NOT NULL CHECK (low_price > 0),
    close_price         numeric(38, 18) NOT NULL CHECK (close_price > 0),
    volume              numeric(38, 18) NOT NULL DEFAULT 0 CHECK (volume >= 0),
    trade_count         bigint NOT NULL DEFAULT 0 CHECK (trade_count >= 0),
    vwap                numeric(38, 18),
    is_complete         boolean NOT NULL DEFAULT false,
    quality_status      text NOT NULL DEFAULT 'unknown' CHECK (quality_status IN ('complete', 'gapped', 'invalid', 'unknown')),
    source_run_id       uuid REFERENCES market_data.ingestion_runs (run_id),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (instrument_id, interval_seconds, bar_start),
    CHECK (bar_end > bar_start),
    CHECK (high_price >= GREATEST(open_price, close_price)),
    CHECK (low_price <= LEAST(open_price, close_price)),
    CHECK (vwap IS NULL OR vwap > 0)
);

CREATE INDEX IF NOT EXISTS bars_lookup_idx
    ON market_data.bars (instrument_id, interval_seconds, bar_start DESC);

-- Persisted quality findings so backtests can explain which records were excluded.
CREATE TABLE IF NOT EXISTS market_data.data_quality_events (
    quality_event_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id             uuid NOT NULL REFERENCES market_data.ingestion_runs (run_id),
    instrument_id      bigint REFERENCES market_data.instruments (instrument_id),
    event_time         timestamptz,
    event_type         text NOT NULL CHECK (event_type IN ('duplicate', 'gap', 'out_of_order', 'invalid', 'mapping_error', 'clock_skew', 'stale_feed', 'other')),
    severity           text NOT NULL CHECK (severity IN ('info', 'warning', 'error')),
    source_sequence_no bigint,
    details            jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS quality_events_run_idx
    ON market_data.data_quality_events (run_id, created_at);
CREATE INDEX IF NOT EXISTS quality_events_instrument_time_idx
    ON market_data.data_quality_events (instrument_id, event_time);

COMMIT;

-- Example managed monthly partition. Create partitions ahead of imports in production:
-- CREATE TABLE market_data.trades_2026_01
--     PARTITION OF market_data.trades
--     FOR VALUES FROM ('2026-01-01 00:00:00+00') TO ('2026-02-01 00:00:00+00');
--
-- CREATE TABLE market_data.quotes_2026_01
--     PARTITION OF market_data.quotes
--     FOR VALUES FROM ('2026-01-01 00:00:00+00') TO ('2026-02-01 00:00:00+00');
--
-- Once rows are loaded into a managed partition, move them out of the default
-- partition before creating the next range partition.
