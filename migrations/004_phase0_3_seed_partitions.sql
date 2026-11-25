-- Managed partitions and deterministic local seed catalog for the initial BTCUSDT slice.

BEGIN;

CREATE TABLE IF NOT EXISTS market_data.trades_2026_11
    PARTITION OF market_data.trades
    FOR VALUES FROM ('2026-11-01 00:00:00+00') TO ('2026-12-01 00:00:00+00');

CREATE TABLE IF NOT EXISTS market_data.quotes_2026_11
    PARTITION OF market_data.quotes
    FOR VALUES FROM ('2026-11-01 00:00:00+00') TO ('2026-12-01 00:00:00+00');

INSERT INTO market_data.venues
    (venue_id, display_name, asset_class, timezone_name, data_license_ref, enabled)
VALUES
    ('binance', 'Binance public market data', 'crypto', 'UTC', 'provider terms pending verification', true)
ON CONFLICT (venue_id) DO UPDATE
SET display_name = EXCLUDED.display_name,
    asset_class = EXCLUDED.asset_class,
    timezone_name = EXCLUDED.timezone_name,
    data_license_ref = EXCLUDED.data_license_ref,
    enabled = EXCLUDED.enabled;

INSERT INTO market_data.ingestion_sources
    (source_id, source_name, source_kind, provider_name, venue_id, endpoint,
     license_reference, asset_class, feed_type, terms_status, redistribution_allowed, enabled, configuration)
VALUES
    ('2a111111-1111-4111-8111-111111111111', 'binance_public_historical', 'historical_dump', 'binance',
     'binance', 'https://data.binance.vision/', 'provider terms pending verification',
     'crypto', 'historical_dump', 'unverified', NULL, true,
     '{"symbol":"BTCUSDT","canonical_symbol":"BTC-USDT","fixture_start":"2026-11-20","fixture_end":"2026-11-24"}'::jsonb)
ON CONFLICT (source_id) DO UPDATE
SET source_name = EXCLUDED.source_name,
    source_kind = EXCLUDED.source_kind,
    provider_name = EXCLUDED.provider_name,
    venue_id = EXCLUDED.venue_id,
    endpoint = EXCLUDED.endpoint,
    license_reference = EXCLUDED.license_reference,
    asset_class = EXCLUDED.asset_class,
    feed_type = EXCLUDED.feed_type,
    terms_status = EXCLUDED.terms_status,
    redistribution_allowed = EXCLUDED.redistribution_allowed,
    enabled = EXCLUDED.enabled,
    configuration = EXCLUDED.configuration;

INSERT INTO market_data.instruments
    (venue_id, venue_symbol, canonical_symbol, asset_class, base_currency, quote_currency,
     price_scale, quantity_scale, tick_size, lot_size, contract_multiplier, enabled, metadata)
VALUES
    ('binance', 'BTCUSDT', 'BTC-USDT', 'crypto', 'BTC', 'USDT',
     8, 8, 0.00000001, 0.00000001, 1, true,
     '{"valuation_currency":"USDT","usd_conversion_required":true,"fixture_only":true}'::jsonb)
ON CONFLICT (venue_id, venue_symbol) DO UPDATE
SET canonical_symbol = EXCLUDED.canonical_symbol,
    asset_class = EXCLUDED.asset_class,
    base_currency = EXCLUDED.base_currency,
    quote_currency = EXCLUDED.quote_currency,
    enabled = EXCLUDED.enabled,
    metadata = EXCLUDED.metadata;

COMMIT;
