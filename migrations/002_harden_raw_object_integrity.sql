-- Require verified checksums and guarded lifecycle transitions for raw objects.
BEGIN;

ALTER TABLE market_data.raw_objects
    ADD CONSTRAINT raw_objects_checksum_sha256_format
    CHECK (checksum_sha256 IS NULL OR checksum_sha256 ~ '^[0-9a-f]{64}$');

ALTER TABLE market_data.raw_objects
    ADD CONSTRAINT raw_objects_validated_checksum_required
    CHECK (
        status IN ('discovered', 'rejected')
        OR checksum_sha256 IS NOT NULL
    );

CREATE TABLE market_data.trade_identities (
    instrument_id      bigint NOT NULL REFERENCES market_data.instruments (instrument_id),
    deduplication_key  text NOT NULL,
    event_time         timestamptz NOT NULL,
    source_run_id      uuid NOT NULL REFERENCES market_data.ingestion_runs (run_id),
    created_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (instrument_id, deduplication_key)
);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM market_data.trades
         GROUP BY instrument_id, deduplication_key
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION 'duplicate trade identities must be reconciled before migration 002';
    END IF;
END;
$$;

INSERT INTO market_data.trade_identities (instrument_id, deduplication_key, event_time, source_run_id)
SELECT instrument_id, deduplication_key, event_time, source_run_id
  FROM market_data.trades;

CREATE OR REPLACE FUNCTION market_data.guard_raw_object_update()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.raw_object_id <> NEW.raw_object_id
       OR OLD.source_id <> NEW.source_id
       OR OLD.object_uri <> NEW.object_uri
       OR OLD.checksum_sha256 IS DISTINCT FROM NEW.checksum_sha256
       OR OLD.byte_size IS DISTINCT FROM NEW.byte_size THEN
        RAISE EXCEPTION 'raw object identity and checksum are immutable';
    END IF;

    IF OLD.status = 'discovered' AND NEW.status NOT IN ('discovered', 'validated', 'rejected') THEN
        RAISE EXCEPTION 'invalid raw object transition from discovered to %', NEW.status;
    ELSIF OLD.status = 'validated' AND NEW.status NOT IN ('validated', 'imported', 'rejected') THEN
        RAISE EXCEPTION 'invalid raw object transition from validated to %', NEW.status;
    ELSIF OLD.status = 'imported' AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'terminal raw object status cannot change from imported';
    ELSIF OLD.status = 'rejected' AND NEW.status NOT IN ('rejected', 'validated') THEN
        RAISE EXCEPTION 'invalid raw object transition from rejected to %', NEW.status;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER raw_objects_guard_update
BEFORE UPDATE ON market_data.raw_objects
FOR EACH ROW EXECUTE FUNCTION market_data.guard_raw_object_update();

CREATE OR REPLACE FUNCTION market_data.transition_raw_object(
    p_raw_object_id uuid,
    p_source_id uuid,
    p_target_status text,
    p_checksum_sha256 text DEFAULT NULL,
    p_byte_size bigint DEFAULT NULL
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = market_data, pg_catalog
AS $$
DECLARE
    current_status text;
BEGIN
    IF session_user <> 'x2_ingestion' THEN
        RAISE EXCEPTION 'raw-object transitions require the x2_ingestion role';
    END IF;
    SELECT status INTO current_status
      FROM market_data.raw_objects
     WHERE raw_object_id = p_raw_object_id AND source_id = p_source_id
     FOR UPDATE;
    IF current_status IS NULL THEN
        RAISE EXCEPTION 'raw object not found';
    END IF;
    IF p_target_status NOT IN ('validated', 'imported', 'rejected') THEN
        RAISE EXCEPTION 'invalid target raw-object status';
    END IF;
    IF current_status = 'imported' AND p_target_status = 'validated' THEN
        RETURN;
    END IF;
    IF p_checksum_sha256 IS NULL THEN
        UPDATE market_data.raw_objects SET status = p_target_status
         WHERE raw_object_id = p_raw_object_id AND source_id = p_source_id;
    ELSE
        UPDATE market_data.raw_objects
           SET checksum_sha256 = p_checksum_sha256, byte_size = p_byte_size, status = p_target_status
         WHERE raw_object_id = p_raw_object_id AND source_id = p_source_id;
    END IF;
END;
$$;

REVOKE UPDATE, DELETE ON market_data.raw_objects FROM PUBLIC;
REVOKE ALL ON FUNCTION market_data.transition_raw_object(uuid, uuid, text, text, bigint) FROM PUBLIC;
GRANT USAGE ON SCHEMA market_data TO x2_ingestion;
GRANT SELECT, INSERT ON market_data.raw_objects TO x2_ingestion;
GRANT SELECT, INSERT, UPDATE ON market_data.venues, market_data.instruments,
    market_data.ingestion_sources, market_data.ingestion_runs,
    market_data.ingestion_checkpoints, market_data.trades,
    market_data.trade_identities, market_data.data_quality_events TO x2_ingestion;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA market_data TO x2_ingestion;
GRANT EXECUTE ON FUNCTION market_data.transition_raw_object(uuid, uuid, text, text, bigint) TO x2_ingestion;

COMMIT;
