-- Local-only role separation: migrations run as admin; ingestion uses a restricted role.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'x2_ingestion') THEN
        CREATE ROLE x2_ingestion LOGIN PASSWORD 'x2_ingestion_local_only';
    END IF;
END
$$;

GRANT CONNECT ON DATABASE x2_trading TO x2_ingestion;
