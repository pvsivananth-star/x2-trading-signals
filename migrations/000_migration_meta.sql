-- Migration bookkeeping used by the local application runner.
BEGIN;

CREATE SCHEMA IF NOT EXISTS app_meta;

CREATE TABLE IF NOT EXISTS app_meta.schema_migrations (
    filename     text PRIMARY KEY,
    applied_at   timestamptz NOT NULL DEFAULT now()
);

COMMIT;
