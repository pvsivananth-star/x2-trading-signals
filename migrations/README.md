# Database migrations

This project uses plain, ordered PostgreSQL SQL migrations while the application layer is being built. The migrations intentionally avoid a framework dependency so they can run from a local PostgreSQL instance, CI, AWS RDS, or Azure Database for PostgreSQL.

## Initial migrations

- `000_migration_meta.sql` creates migration bookkeeping.
- `001_initial_market_data.sql` creates the `market_data` schema and the initial ingestion tables:
  - venue and instrument catalogs
  - source and raw-object provenance
  - ingestion runs and resumable checkpoints
  - partitioned trades and quotes
  - derived bars
  - data-quality events
- `002_harden_raw_object_integrity.sql` requires raw checksums, guards raw-object lifecycle transitions, adds a global trade-identity registry, and restricts transitions to the `x2_ingestion` role.

The application runner records every applied filename in `app_meta.schema_migrations`. Future migrations should be forward-only and should not edit an already-applied migration.

## Applying locally

```bash
export DATABASE_URL=postgresql://x2:x2_local_only@127.0.0.1:5432/x2_trading
python -m app.cli migrate
```

For a new migration, use the next numeric prefix:

```text
002_add_<short_description>.sql
003_add_<short_description>.sql
```

Apply migrations in lexical order and record the applied filename in the application's migration table once the application runtime is added. Until then, deployment scripts should fail if a migration exits non-zero.

## Partition management

The initial migration creates default partitions for `trades` and `quotes` so ingestion cannot fail solely because a managed date partition has not been created. Production ingestion should create time-range partitions ahead of a backfill or live run and move data out of the default partition as part of maintenance.

## Schema snapshot

`schemas/market_data.sql` is a readable schema snapshot for tooling and review. `migrations/001_initial_market_data.sql` is the installation entry point. When the schema changes, add a new migration and update the snapshot in the same change.
