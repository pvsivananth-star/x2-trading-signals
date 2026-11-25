"""Immutable source-object and normalized Parquet storage."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import shutil
import tempfile
from typing import Iterable, Protocol

from app.domain.events import NormalizedTrade


class ArchiveError(RuntimeError):
    """Raised when an archive invariant cannot be satisfied."""


@dataclass(frozen=True, slots=True)
class PreservedSource:
    raw_object_id: str
    path: Path
    object_uri: str
    original_filename: str
    checksum_sha256: str
    byte_size: int


class NormalizedWriter(Protocol):
    def write(
        self, events: Iterable[NormalizedTrade], raw_object_id: str, batch_number: int = 0
    ) -> list[Path]: ...


class RawObjectStore:
    """Store native source bytes under a content-addressed, no-overwrite path."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def preserve(self, source_path: Path, raw_object_id: str, *, venue_symbol: str) -> PreservedSource:
        if not source_path.is_file():
            raise ArchiveError(f"source file does not exist: {source_path}")
        checksum, byte_size = sha256_file(source_path)
        target_dir = (
            self.root
            / "provider=binance"
            / f"symbol={_safe_path_part(venue_symbol)}"
            / f"sha256={checksum}"
        )
        target = target_dir / "object"
        target_dir.mkdir(parents=True, exist_ok=True)
        if target.exists():
            _assert_same_file(target, checksum, byte_size)
        else:
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    dir=target_dir, prefix=".object-", suffix=".partial", delete=False
                ) as output:
                    temporary = Path(output.name)
                    with source_path.open("rb") as source:
                        shutil.copyfileobj(source, output)
                    output.flush()
                    os.fsync(output.fileno())
                copied_checksum, copied_size = sha256_file(temporary)
                if copied_checksum != checksum or copied_size != byte_size:
                    raise ArchiveError("source changed while being copied")
                try:
                    os.link(temporary, target)
                except FileExistsError:
                    _assert_same_file(target, checksum, byte_size)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        object_uri = target.relative_to(self.root).as_posix()
        return PreservedSource(
            raw_object_id, target, object_uri, source_path.name, checksum, byte_size
        )


class ParquetNormalizedWriter:
    """Write normalized events as partitioned Parquet using optional PyArrow."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def write(
        self, events: Iterable[NormalizedTrade], raw_object_id: str, batch_number: int = 0
    ) -> list[Path]:
        rows = [event.as_archive_row() for event in events]
        if not rows:
            return []
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise ArchiveError(
                "Parquet output requires the runtime extra: pip install -e '.[runtime]'"
            ) from exc

        partitions: dict[tuple[str, str], list[dict[str, object]]] = {}
        for row in rows:
            event_time = row["event_time"]
            assert hasattr(event_time, "date")
            key = (str(row["venue_symbol"]), event_time.date().isoformat())
            partitions.setdefault(key, []).append(row)

        paths: list[Path] = []
        for (venue_symbol, event_date), partition_rows in partitions.items():
            directory = (
                self.root
                / f"venue={_safe_path_part(str(partition_rows[0]['venue_id']))}"
                / f"symbol={_safe_path_part(venue_symbol)}"
                / f"date={event_date}"
            )
            directory.mkdir(parents=True, exist_ok=True)
            object_dir = directory / f"raw_object={_safe_path_part(raw_object_id)}"
            object_dir.mkdir(parents=True, exist_ok=True)
            path = object_dir / f"part-{batch_number:06d}.parquet"
            table = pa.Table.from_pylist(partition_rows)
            if path.exists():
                # Receipt time and run ID are run metadata, not logical event identity.
                existing = pq.read_table(path)
                if _logical_rows(existing.to_pylist()) != _logical_rows(partition_rows):
                    raise ArchiveError(f"normalized archive collision: {path}")
            else:
                temporary: Path | None = None
                try:
                    with tempfile.NamedTemporaryFile(
                        dir=object_dir, prefix=".part-", suffix=".parquet.partial", delete=False
                    ) as output:
                        temporary = Path(output.name)
                    pq.write_table(table, temporary, compression="zstd")
                    with temporary.open("rb") as output:
                        os.fsync(output.fileno())
                    try:
                        os.link(temporary, path)
                    except FileExistsError:
                        existing = pq.read_table(path)
                        if _logical_rows(existing.to_pylist()) != _logical_rows(partition_rows):
                            raise ArchiveError(f"normalized archive collision: {path}")
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
            paths.append(path)
        return paths


def _assert_same_file(path: Path, checksum: str, byte_size: int) -> None:
    existing_checksum, existing_size = sha256_file(path)
    if existing_checksum != checksum or existing_size != byte_size:
        raise ArchiveError(f"immutable raw object collision: {path}")


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> tuple[str, int]:
    digest = sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(chunk_size):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _logical_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    ignored = {"received_time", "source_run_id"}
    return [{key: value for key, value in row.items() if key not in ignored} for row in rows]


def _safe_path_part(value: str) -> str:
    safe = "".join(character if character.isalnum() or character in "._-" else "_" for character in value)
    return safe or "unknown"
