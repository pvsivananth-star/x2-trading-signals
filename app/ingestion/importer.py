"""Restartable, idempotent historical dump importer."""

from __future__ import annotations

import sqlite3
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid4, uuid5

from app.domain.events import NormalizedTrade, RawTradeEvent
from app.ingestion.archive import NormalizedWriter, PreservedSource, RawObjectStore


@dataclass(frozen=True, slots=True)
class QualityFinding:
    event_type: str
    severity: str
    details: dict[str, Any] = field(default_factory=dict)
    event_time: datetime | None = None


@dataclass(frozen=True, slots=True)
class ImportResult:
    run_id: str
    raw_object_id: str
    raw_object_path: Path
    checksum_sha256: str
    raw_record_count: int
    accepted_count: int
    duplicate_count: int
    invalid_count: int
    out_of_order_count: int
    gap_count: int
    quality_findings: tuple[QualityFinding, ...]
    normalized_paths: tuple[Path, ...]

    @property
    def completed_with_errors(self) -> bool:
        return bool(self.invalid_count or self.out_of_order_count or self.gap_count)


class TradeDumpAdapter(Protocol):
    venue_id: str
    venue_symbol: str

    def iter_dump_rows(self, path: Path) -> Iterator[dict[str, Any] | list[str]]: ...

    def parse_dump_row(self, row: dict[str, Any] | list[str], received_time: datetime) -> RawTradeEvent: ...


class IngestionRepository(Protocol):
    def begin_run(
        self,
        *,
        source_id: str,
        raw_object_id: str,
        source_uri: str,
        checksum_sha256: str,
        byte_size: int,
        configuration: dict[str, Any],
    ) -> str: ...

    def mark_raw_validated(
        self,
        *,
        source_id: str,
        raw_object_id: str,
        checksum_sha256: str,
        byte_size: int,
    ) -> None: ...

    def upsert_trades(self, *, run_id: str, events: list[NormalizedTrade]) -> list[NormalizedTrade]: ...

    def commit_pending(self) -> None: ...

    def rollback_pending(self) -> None: ...

    def record_quality(self, *, run_id: str, finding: QualityFinding) -> None: ...

    def finish_run(self, *, run_id: str, result: ImportResult) -> None: ...

    def fail_run(self, *, run_id: str, error: str) -> None: ...


class NullRepository:
    """Local dry repository used when PostgreSQL is not configured."""

    def __init__(self) -> None:
        self._keys: set[tuple[str, str]] = set()
        self._pending_keys: list[tuple[str, str]] = []
        self.runs: dict[str, ImportResult] = {}
        self.quality: list[tuple[str, QualityFinding]] = []

    def begin_run(self, **_: Any) -> str:
        return str(uuid4())

    def mark_raw_validated(self, **_: Any) -> None:
        return None

    def upsert_trades(self, *, run_id: str, events: list[NormalizedTrade]) -> list[NormalizedTrade]:
        inserted: list[NormalizedTrade] = []
        for event in events:
            key = (event.canonical_symbol, event.deduplication_key)
            if key in self._keys:
                continue
            self._keys.add(key)
            inserted.append(event)
        return inserted

    def commit_pending(self) -> None:
        return None

    def rollback_pending(self) -> None:
        return None

    def record_quality(self, *, run_id: str, finding: QualityFinding) -> None:
        self.quality.append((run_id, finding))

    def finish_run(self, *, run_id: str, result: ImportResult) -> None:
        self.runs[run_id] = result

    def fail_run(self, *, run_id: str, error: str) -> None:
        return None


class DiskDeduplicationIndex:
    """Disk-backed event identity set so large dumps do not grow Python memory."""

    def __init__(self) -> None:
        self._directory = tempfile.TemporaryDirectory(prefix="x2-import-")
        self._connection = sqlite3.connect(Path(self._directory.name) / "dedupe.sqlite")
        self._connection.execute("PRAGMA journal_mode = OFF")
        self._connection.execute("PRAGMA synchronous = OFF")
        self._connection.execute("CREATE TABLE keys (deduplication_key TEXT PRIMARY KEY)")
        self._connection.commit()

    def add(self, key: str) -> bool:
        cursor = self._connection.execute(
            "INSERT OR IGNORE INTO keys (deduplication_key) VALUES (?)", (key,)
        )
        self._connection.commit()
        return cursor.rowcount == 1

    def close(self) -> None:
        self._connection.close()
        self._directory.cleanup()


class HistoricalDumpImporter:
    """Import a historical trade dump without mutating the source object."""

    def __init__(
        self,
        *,
        adapter: TradeDumpAdapter,
        canonical_symbol: str,
        raw_store: RawObjectStore,
        normalized_writer: NormalizedWriter,
        repository: IngestionRepository,
    ) -> None:
        self.adapter = adapter
        self.canonical_symbol = canonical_symbol
        self.raw_store = raw_store
        self.normalized_writer = normalized_writer
        self.repository = repository

    def import_file(
        self,
        source_path: Path,
        *,
        source_id: str,
        received_time: datetime | None = None,
    ) -> ImportResult:
        source_path = source_path.resolve()
        # Preserve and verify the native bytes first. The source path may be changed
        # by another process after this point, so all parsing uses the preserved copy.
        preserved = self.raw_store.preserve(
            source_path, "pending", venue_symbol=self.adapter.venue_symbol
        )
        raw_object_id = str(uuid5(NAMESPACE_URL, f"{source_id}:{preserved.checksum_sha256}"))
        preserved = replace(preserved, raw_object_id=raw_object_id)
        run_id = self.repository.begin_run(
            source_id=source_id,
            raw_object_id=raw_object_id,
            source_uri=preserved.object_uri,
            checksum_sha256=preserved.checksum_sha256,
            byte_size=preserved.byte_size,
            configuration={
                "provider": self.adapter.venue_id,
                "venue_symbol": self.adapter.venue_symbol,
                "canonical_symbol": self.canonical_symbol,
            },
        )
        try:
            self.repository.mark_raw_validated(
                source_id=source_id,
                raw_object_id=raw_object_id,
                checksum_sha256=preserved.checksum_sha256,
                byte_size=preserved.byte_size,
            )
            result = self._process(
                source_path=preserved.path,
                source_id=source_id,
                raw_object_id=raw_object_id,
                run_id=run_id,
                received_time=received_time or datetime.now(UTC),
                preserved=preserved,
            )
            self.repository.finish_run(run_id=run_id, result=result)
            return result
        except Exception as exc:
            self.repository.rollback_pending()
            self.repository.fail_run(run_id=run_id, error=str(exc))
            raise

    def _process(
        self,
        *,
        source_path: Path,
        source_id: str,
        raw_object_id: str,
        run_id: str,
        received_time: datetime,
        preserved: PreservedSource,
    ) -> ImportResult:
        batch_size = 10_000
        batch: list[NormalizedTrade] = []
        seen_keys = DiskDeduplicationIndex()
        normalized_paths: list[Path] = []
        findings: list[QualityFinding] = []
        last_event_time: datetime | None = None
        last_trade_id: int | None = None
        raw_count = duplicate_count = inserted_count = invalid_count = 0
        out_of_order_count = gap_count = batch_number = 0

        def record_finding(finding: QualityFinding) -> None:
            if len(findings) < 1_000:
                findings.append(finding)
            self.repository.record_quality(run_id=run_id, finding=finding)

        def flush_batch() -> None:
            nonlocal inserted_count, duplicate_count, batch_number
            if not batch:
                return
            inserted_events = self.repository.upsert_trades(run_id=run_id, events=batch)
            inserted_count += len(inserted_events)
            duplicate_count += len(batch) - len(inserted_events)
            try:
                normalized_paths.extend(
                    self.normalized_writer.write(
                        inserted_events, raw_object_id, batch_number=batch_number
                    )
                )
            except Exception:
                self.repository.rollback_pending()
                raise
            self.repository.commit_pending()
            batch.clear()
            batch_number += 1

        for row in self.adapter.iter_dump_rows(source_path):
            raw_count += 1
            try:
                raw_event = self.adapter.parse_dump_row(row, received_time)
                if not seen_keys.add(raw_event.deduplication_key):
                    duplicate_count += 1
                    record_finding(
                        QualityFinding(
                            "duplicate",
                            "info",
                            {"deduplication_key": raw_event.deduplication_key},
                            raw_event.event_time,
                        )
                    )
                    continue

                if last_event_time is not None and raw_event.event_time < last_event_time:
                    out_of_order_count += 1
                    record_finding(
                        QualityFinding(
                            "out_of_order",
                            "error",
                            {"previous_event_time": last_event_time.isoformat()},
                            raw_event.event_time,
                        )
                    )
                last_event_time = max(last_event_time, raw_event.event_time) if last_event_time else raw_event.event_time

                current_trade_id = _integer_id(raw_event.venue_trade_id)
                if last_trade_id is not None and current_trade_id is not None and current_trade_id > last_trade_id + 1:
                    gap_count += 1
                    record_finding(
                        QualityFinding(
                            "gap",
                            "warning",
                            {"previous_trade_id": last_trade_id, "current_trade_id": current_trade_id},
                            raw_event.event_time,
                        )
                    )
                if current_trade_id is not None:
                    last_trade_id = max(last_trade_id or current_trade_id, current_trade_id)

                batch.append(raw_event.normalize(self.canonical_symbol, raw_object_id, run_id))
                if len(batch) >= batch_size:
                    flush_batch()
            except (TypeError, ValueError, OverflowError) as exc:
                invalid_count += 1
                record_finding(QualityFinding("invalid", "error", {"error": str(exc), "row": repr(row)}))

        flush_batch()
        seen_keys.close()
        return ImportResult(
            run_id=run_id,
            raw_object_id=raw_object_id,
            raw_object_path=preserved.path,
            checksum_sha256=preserved.checksum_sha256,
            raw_record_count=raw_count,
            accepted_count=inserted_count,
            duplicate_count=duplicate_count,
            invalid_count=invalid_count,
            out_of_order_count=out_of_order_count,
            gap_count=gap_count,
            quality_findings=tuple(findings),
            normalized_paths=tuple(normalized_paths),
        )


def _integer_id(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
