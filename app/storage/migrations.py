"""Small forward-only PostgreSQL migration runner."""

from __future__ import annotations

from pathlib import Path


class MigrationError(RuntimeError):
    """Raised when migrations cannot be applied."""


_LOCK_KEY = "x2-trading-signals:migrations"


def apply_migrations(database_url: str, migrations_dir: Path) -> list[str]:
    """Apply unapplied SQL migrations in lexical order and return their names."""

    try:
        import psycopg
    except ImportError as exc:
        raise MigrationError("PostgreSQL migrations require psycopg: pip install -e '.[runtime]'") from exc

    files = sorted(migrations_dir.glob("*.sql"))
    if not files:
        raise MigrationError(f"no SQL migrations found in {migrations_dir}")

    applied: list[str] = []
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (_LOCK_KEY,))
        try:
            connection.execute("CREATE SCHEMA IF NOT EXISTS app_meta")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS app_meta.schema_migrations (
                    filename text PRIMARY KEY,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            existing = {
                row[0]
                for row in connection.execute(
                    "SELECT filename FROM app_meta.schema_migrations"
                ).fetchall()
            }
            for migration in files:
                if migration.name in existing:
                    continue
                sql = migration.read_text(encoding="utf-8")
                try:
                    connection.execute(sql)
                    connection.execute(
                        "INSERT INTO app_meta.schema_migrations (filename) VALUES (%s)",
                        (migration.name,),
                    )
                except Exception as exc:
                    raise MigrationError(f"migration failed: {migration.name}") from exc
                applied.append(migration.name)
        finally:
            connection.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (_LOCK_KEY,))
    return applied
