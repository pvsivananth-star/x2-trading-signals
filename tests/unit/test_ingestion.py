import tempfile
import unittest
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from app.ingestion.archive import RawObjectStore
from app.ingestion.importer import HistoricalDumpImporter, NullRepository
from app.providers.binance_public import BinancePublicTradeAdapter


class MemoryWriter:
    def __init__(self) -> None:
        self.writes: list[tuple[str, list[object]]] = []

    def write(self, events, raw_object_id: str, batch_number: int = 0) -> list[Path]:
        materialized = list(events)
        self.writes.append((raw_object_id, materialized))
        return []


class HistoricalImportTests(unittest.TestCase):
    def _make_dump(self, directory: str, content: str) -> Path:
        path = Path(directory) / "BTCUSDT-trades.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("BTCUSDT-trades.csv", content)
        return path

    def _make_importer(self, directory: str, repository: NullRepository, writer: MemoryWriter) -> HistoricalDumpImporter:
        return HistoricalDumpImporter(
            adapter=BinancePublicTradeAdapter("BTCUSDT"),
            canonical_symbol="BTC-USDT",
            raw_store=RawObjectStore(Path(directory) / "data" / "raw"),
            normalized_writer=writer,
            repository=repository,
        )

    def test_rerunning_same_dump_is_idempotent_in_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dump = self._make_dump(
                directory,
                "id,price,qty,time,is_buyer_maker\n1,10,2,1704067200000,false\n",
            )
            repository = NullRepository()
            writer = MemoryWriter()
            importer = self._make_importer(directory, repository, writer)
            source_id = str(uuid5(NAMESPACE_URL, "test-source"))
            first = importer.import_file(
                dump, source_id=source_id, received_time=datetime(2024, 1, 1, tzinfo=UTC)
            )
            second = importer.import_file(
                dump, source_id=source_id, received_time=datetime(2024, 1, 1, tzinfo=UTC)
            )

        self.assertEqual(first.accepted_count, 1)
        self.assertEqual(second.accepted_count, 0)
        self.assertEqual(second.duplicate_count, 1)
        self.assertEqual(first.checksum_sha256, second.checksum_sha256)
        self.assertEqual(first.raw_object_path, second.raw_object_path)
        self.assertEqual(len(writer.writes), 2)

    def test_quality_counts_capture_invalid_order_and_gap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dump = self._make_dump(
                directory,
                "id,price,qty,time,is_buyer_maker\n"
                "1,10,2,1704067200000,false\n"
                "3,11,2,1704067202000,false\n"
                "2,12,2,1704067201000,false\n"
                "4,0,2,1704067203000,false\n",
            )
            repository = NullRepository()
            result = self._make_importer(directory, repository, MemoryWriter()).import_file(
                dump,
                source_id=str(uuid5(NAMESPACE_URL, "quality-source")),
                received_time=datetime(2024, 1, 1, tzinfo=UTC),
            )

        self.assertEqual(result.raw_record_count, 4)
        self.assertEqual(result.accepted_count, 3)
        self.assertEqual(result.invalid_count, 1)
        self.assertEqual(result.out_of_order_count, 1)
        self.assertEqual(result.gap_count, 1)
        self.assertTrue(result.completed_with_errors)

    def test_duplicate_across_batches_is_not_archived_twice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            rows = ["id,price,qty,time,is_buyer_maker"]
            rows.extend(
                f"{trade_id},10,1,{1704067200000 + trade_id},false"
                for trade_id in range(1, 10_001)
            )
            rows.append("1,10,1,1704067210001,false")
            dump = self._make_dump(directory, "\n".join(rows) + "\n")
            repository = NullRepository()
            writer = MemoryWriter()
            result = self._make_importer(directory, repository, writer).import_file(
                dump,
                source_id=str(uuid5(NAMESPACE_URL, "batch-source")),
                received_time=datetime(2024, 1, 1, tzinfo=UTC),
            )

        self.assertEqual(result.accepted_count, 10_000)
        self.assertEqual(result.duplicate_count, 1)
        self.assertEqual(sum(len(events) for _, events in writer.writes), 10_000)

    def test_raw_source_is_content_addressed_and_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.csv"
            source.write_text("a,b\n1,2\n", encoding="utf-8")
            store = RawObjectStore(Path(directory) / "raw")
            first = store.preserve(source, "raw-id", venue_symbol="BTCUSDT")
            second = store.preserve(source, "raw-id", venue_symbol="BTCUSDT")
            self.assertEqual(first.path, second.path)
            self.assertTrue(first.path.exists())
            self.assertIn("sha256=", str(first.path))


if __name__ == "__main__":
    unittest.main()
