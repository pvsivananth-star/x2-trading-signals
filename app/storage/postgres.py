"""PostgreSQL persistence for ingestion provenance and normalized trades."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.domain.events import NormalizedTrade
from app.ingestion.importer import ImportResult, IngestionRepository, QualityFinding
from app.providers.registry import resolve_source


class PostgresRepositoryError(RuntimeError):
    """Raised when PostgreSQL cannot preserve an ingestion invariant."""


class PostgresIngestionRepository(IngestionRepository):
    """Persistence adapter for the existing ``market_data`` schema."""

    def __init__(
        self,
        database_url: str,
        *,
        venue_id: str,
        venue_symbol: str,
        canonical_symbol: str,
        source_name: str = "binance_public_historical",
    ) -> None:
        try:
            import psycopg
        except ImportError as exc:
            raise PostgresRepositoryError(
                "PostgreSQL ingestion requires psycopg: pip install -e '.[runtime]'"
            ) from exc
        self._connection = psycopg.connect(database_url, autocommit=False)
        self.venue_id = venue_id
        self.venue_symbol = venue_symbol
        self.canonical_symbol = canonical_symbol
        self.source_name = source_name
        self.instrument_id: int | None = None

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "PostgresIngestionRepository":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def begin_run(
        self,
        *,
        source_id: str,
        raw_object_id: str,
        source_uri: str,
        checksum_sha256: str,
        byte_size: int,
        configuration: dict[str, Any],
    ) -> str:
        self._ensure_catalog(UUID(source_id), configuration)
        raw_id = UUID(raw_object_id)
        run_id = uuid4()
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO market_data.raw_objects
                        (raw_object_id, source_id, object_uri, checksum_sha256, byte_size, compression, status, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, 'discovered', %s)
                    ON CONFLICT (raw_object_id) DO NOTHING
                    """,
                    (
                        raw_id,
                        UUID(source_id),
                        source_uri,
                        checksum_sha256,
                        byte_size,
                        _compression_from_path(source_uri),
                        json.dumps({"provider": configuration.get("provider"), "symbol": self.venue_symbol}),
                    ),
                )
                cursor.execute(
                    "SELECT checksum_sha256, object_uri FROM market_data.raw_objects WHERE raw_object_id = %s",
                    (raw_id,),
                )
                existing_checksum, existing_uri = cursor.fetchone()
                if existing_checksum != checksum_sha256 or existing_uri != source_uri:
                    raise PostgresRepositoryError("raw object identity collision or checksum mismatch")
                cursor.execute(
                    """
                    INSERT INTO market_data.ingestion_runs
                        (run_id, source_id, instrument_id, raw_object_id, run_kind, status, started_at, configuration)
                    VALUES (%s, %s, %s, %s, 'backfill', 'running', now(), %s)
                    """,
                    (
                        run_id,
                        UUID(source_id),
                        self.instrument_id,
                        raw_id,
                        json.dumps(configuration),
                    ),
                )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return str(run_id)

    def mark_raw_validated(
        self,
        *,
        source_id: str,
        raw_object_id: str,
        checksum_sha256: str,
        byte_size: int,
    ) -> None:
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    "SELECT market_data.transition_raw_object(%s, %s, 'validated', %s, %s)",
                    (UUID(raw_object_id), UUID(source_id), checksum_sha256, byte_size),
                )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def upsert_trades(self, *, run_id: str, events: list[NormalizedTrade]) -> list[NormalizedTrade]:
        if self.instrument_id is None:
            raise PostgresRepositoryError("instrument catalog is not initialized")
        inserted_events: list[NormalizedTrade] = []
        try:
            with self._connection.cursor() as cursor:
                for event in events:
                    cursor.execute(
                        """
                        INSERT INTO market_data.trade_identities
                            (instrument_id, deduplication_key, event_time, source_run_id)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (instrument_id, deduplication_key) DO NOTHING
                        """,
                        (self.instrument_id, event.deduplication_key, event.event_time, UUID(run_id)),
                    )
                    if cursor.rowcount != 1:
                        continue
                    cursor.execute(
                        """
                        INSERT INTO market_data.trades
                            (instrument_id, event_time, received_time, venue_trade_id, sequence_no,
                             deduplication_key, price, quantity, aggressor_side, source_run_id, payload_ref)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            self.instrument_id,
                            event.event_time,
                            event.received_time,
                            event.venue_trade_id,
                            event.sequence_no,
                            event.deduplication_key,
                            event.price,
                            event.quantity,
                            event.aggressor_side,
                            UUID(run_id),
                            event.source_object_id,
                        ),
                    )
                    if cursor.rowcount == 1:
                        inserted_events.append(event)
        except Exception:
            self._connection.rollback()
            raise
        return inserted_events

    def commit_pending(self) -> None:
        self._connection.commit()

    def rollback_pending(self) -> None:
        self._connection.rollback()

    def record_quality(self, *, run_id: str, finding: QualityFinding) -> None:
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO market_data.data_quality_events
                        (run_id, instrument_id, event_time, event_type, severity, details)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        UUID(run_id),
                        self.instrument_id,
                        finding.event_time,
                        finding.event_type,
                        finding.severity,
                        json.dumps(finding.details, default=str),
                    ),
                )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def finish_run(self, *, run_id: str, result: ImportResult) -> None:
        status = "completed_with_errors" if result.completed_with_errors else "completed"
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE market_data.ingestion_runs
                       SET status = %s, finished_at = now(), raw_record_count = %s,
                           accepted_count = %s, duplicate_count = %s, invalid_count = %s,
                           out_of_order_count = %s, gap_count = %s
                     WHERE run_id = %s
                    """,
                    (
                        status,
                        result.raw_record_count,
                        result.accepted_count,
                        result.duplicate_count,
                        result.invalid_count,
                        result.out_of_order_count,
                        result.gap_count,
                        UUID(run_id),
                    ),
                )
                cursor.execute(
                    """
                    SELECT market_data.transition_raw_object(raw.raw_object_id, raw.source_id, 'imported')
                      FROM market_data.raw_objects AS raw
                      JOIN market_data.ingestion_runs AS run
                        ON run.raw_object_id = raw.raw_object_id AND run.source_id = raw.source_id
                     WHERE run.run_id = %s
                    """,
                    (UUID(run_id),),
                )
                if cursor.rowcount != 1:
                    raise PostgresRepositoryError("raw object was not found at run completion")
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def fail_run(self, *, run_id: str, error: str) -> None:
        # Discard any uncommitted batch before recording the terminal run state.
        self._connection.rollback()
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE market_data.ingestion_runs
                       SET status = 'failed', finished_at = now(), error_message = %s
                     WHERE run_id = %s
                    """,
                    (error[:4000], UUID(run_id)),
                )
                cursor.execute(
                    """
                    SELECT market_data.transition_raw_object(raw.raw_object_id, raw.source_id, 'rejected')
                      FROM market_data.raw_objects AS raw
                      JOIN market_data.ingestion_runs AS run
                        ON run.raw_object_id = raw.raw_object_id AND run.source_id = raw.source_id
                     WHERE run.run_id = %s AND raw.status IN ('discovered', 'validated')
                    """,
                    (UUID(run_id),),
                )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def _ensure_catalog(self, source_id: UUID, configuration: dict[str, Any]) -> None:
        try:
            source_definition = resolve_source("binance")
            instrument_asset_class = "crypto" if self.venue_id == "binance" else "other"
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO market_data.venues
                        (venue_id, display_name, asset_class, timezone_name, data_license_ref)
                    VALUES (%s, %s, %s, 'UTC', %s)
                    ON CONFLICT (venue_id) DO UPDATE
                        SET display_name = EXCLUDED.display_name,
                            asset_class = EXCLUDED.asset_class,
                            data_license_ref = EXCLUDED.data_license_ref
                    """,
                    (
                        self.venue_id,
                        "Binance public market data",
                        instrument_asset_class,
                        source_definition.license_reference,
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO market_data.ingestion_sources
                        (source_id, source_name, source_kind, provider_name, venue_id, endpoint,
                         license_reference, asset_class, feed_type, terms_status, redistribution_allowed, configuration)
                    VALUES (%s, %s, 'historical_dump', 'binance', %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (source_id) DO UPDATE
                        SET asset_class = EXCLUDED.asset_class,
                            feed_type = EXCLUDED.feed_type,
                            terms_status = EXCLUDED.terms_status,
                            redistribution_allowed = EXCLUDED.redistribution_allowed
                    """,
                    (
                        source_id,
                        self.source_name,
                        self.venue_id,
                        source_definition.endpoint,
                        source_definition.license_reference,
                        source_definition.asset_class,
                        source_definition.feed_type,
                        source_definition.terms_status,
                        source_definition.redistribution_allowed,
                        json.dumps(configuration),
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO market_data.instruments
                        (venue_id, venue_symbol, canonical_symbol, asset_class, base_currency, quote_currency,
                         price_scale, quantity_scale, tick_size, lot_size, metadata)
                    VALUES (%s, %s, %s, %s, 'BTC', 'USDT', 8, 8, 0.00000001, 0.00000001, %s)
                    ON CONFLICT (venue_id, venue_symbol) DO UPDATE
                        SET canonical_symbol = EXCLUDED.canonical_symbol
                    RETURNING instrument_id
                    """,
                    (
                        self.venue_id,
                        self.venue_symbol,
                        self.canonical_symbol,
                        instrument_asset_class,
                        json.dumps({"valuation_currency": "USDT", "usd_conversion_required": True}),
                    ),
                )
                self.instrument_id = cursor.fetchone()[0]
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise


def _compression_from_path(path: str) -> str | None:
    suffix = Path(path).suffix.lower()
    return {".zip": "zip", ".gz": "gzip", ".bz2": "bzip2"}.get(suffix)
