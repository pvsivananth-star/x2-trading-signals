-- Provider-neutral source and news-feed registry for EQ, FX, Metal, and Crypto.

BEGIN;

ALTER TABLE market_data.ingestion_sources
    ADD COLUMN IF NOT EXISTS asset_class text,
    ADD COLUMN IF NOT EXISTS feed_type text,
    ADD COLUMN IF NOT EXISTS terms_status text NOT NULL DEFAULT 'unverified',
    ADD COLUMN IF NOT EXISTS redistribution_allowed boolean;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ingestion_sources_asset_class_check'
          AND conrelid = 'market_data.ingestion_sources'::regclass
    ) THEN
        ALTER TABLE market_data.ingestion_sources
            ADD CONSTRAINT ingestion_sources_asset_class_check
            CHECK (asset_class IS NULL OR asset_class IN (
                'equity', 'etf', 'metal', 'future', 'forex', 'crypto', 'option', 'bond', 'index', 'other'
            ));
    END IF;
END $$;

ALTER TABLE market_data.ingestion_sources
    ADD CONSTRAINT ingestion_sources_feed_type_check
    CHECK (feed_type IS NULL OR feed_type IN (
        'historical_dump', 'tick_rest', 'tick_websocket', 'ohlc_rest', 'rss', 'news_api', 'calendar', 'reference'
    ));

ALTER TABLE market_data.ingestion_sources
    ADD CONSTRAINT ingestion_sources_terms_status_check
    CHECK (terms_status IN ('unverified', 'reviewed', 'approved', 'restricted'));

CREATE TABLE IF NOT EXISTS market_data.instrument_aliases (
    alias_id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    instrument_id       bigint NOT NULL REFERENCES market_data.instruments (instrument_id),
    provider_name       text NOT NULL,
    provider_symbol     text NOT NULL,
    canonical_symbol    text NOT NULL,
    asset_class         text NOT NULL CHECK (asset_class IN (
        'equity', 'etf', 'metal', 'future', 'forex', 'crypto', 'option', 'bond', 'index', 'other'
    )),
    metadata            jsonb NOT NULL DEFAULT '{}'::jsonb,
    enabled             boolean NOT NULL DEFAULT true,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (provider_name, provider_symbol, canonical_symbol)
);

CREATE TABLE IF NOT EXISTS market_data.feed_events (
    feed_event_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id           uuid NOT NULL REFERENCES market_data.ingestion_sources (source_id),
    instrument_id       bigint REFERENCES market_data.instruments (instrument_id),
    event_time          timestamptz NOT NULL,
    received_time       timestamptz NOT NULL,
    event_type          text NOT NULL CHECK (event_type IN (
        'headline', 'press_release', 'economic_event', 'calendar_update', 'provider_status', 'other'
    )),
    headline            text,
    body_summary        text,
    external_url        text,
    language_code       text,
    relevance_score     numeric(5, 4) CHECK (relevance_score IS NULL OR (relevance_score >= 0 AND relevance_score <= 1)),
    payload_ref         text,
    metadata            jsonb NOT NULL DEFAULT '{}'::jsonb,
    deduplication_key   text NOT NULL,
    UNIQUE (source_id, deduplication_key)
);

CREATE INDEX IF NOT EXISTS feed_events_time_idx
    ON market_data.feed_events (event_time DESC);
CREATE INDEX IF NOT EXISTS feed_events_instrument_time_idx
    ON market_data.feed_events (instrument_id, event_time DESC);

COMMIT;
