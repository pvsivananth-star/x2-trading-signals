"""Command-line entry points for local ingestion."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from app.config import Settings
from app.ingestion.archive import ParquetNormalizedWriter, RawObjectStore
from app.ingestion.download import materialize_source
from app.ingestion.importer import HistoricalDumpImporter, NullRepository
from app.providers.binance_public import BinancePublicTradeAdapter
from app.storage.migrations import apply_migrations
from app.storage.postgres import PostgresIngestionRepository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="x2-trading", description="Local-first trading platform tools")
    subparsers = parser.add_subparsers(dest="command", required=True)

    migrate = subparsers.add_parser("migrate", help="apply pending PostgreSQL migrations")
    migrate.add_argument("--database-url", default=None)
    migrate.add_argument("--migrations-dir", type=Path, default=None)

    import_dump = subparsers.add_parser("import-dump", help="import a Binance historical trade dump")
    import_dump.add_argument("source", help="local dump path or allow-listed HTTPS dump URL")
    import_dump.add_argument("--data-dir", type=Path, default=None)
    import_dump.add_argument("--database-url", default=None)
    import_dump.add_argument("--source-id", default=None, help="stable ingestion source UUID")
    import_dump.add_argument("--venue-symbol", default=None)
    import_dump.add_argument("--canonical-symbol", default=None)
    import_dump.add_argument(
        "--no-database",
        action="store_true",
        help="write the archive without PostgreSQL (useful for parser/archive development)",
    )

    subparsers.add_parser("load-fixtures", help="validate the deterministic local market-data fixtures")
    return parser


def main(argv: list[str] | None = None) -> int:
    settings = Settings.from_env()
    args = build_parser().parse_args(argv)
    try:
        if args.command == "migrate":
            database_url = args.database_url or settings.database_url
            if not database_url:
                raise SystemExit("DATABASE_URL or --database-url is required")
            migrations_dir = args.migrations_dir or settings.migrations_dir
            applied = apply_migrations(database_url, migrations_dir)
            print(json.dumps({"applied": applied}))
            return 0

        if args.command == "import-dump":
            return _import_dump(args, settings)
        if args.command == "load-fixtures":
            return _load_fixtures(settings)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - CLI boundary translates all operational failures
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 2


def _import_dump(args: argparse.Namespace, settings: Settings) -> int:
    if not args.venue_symbol and not settings.venue_symbol:
        raise SystemExit("--venue-symbol or VENUE_SYMBOL is required for provider-specific dumps")
    venue_symbol = args.venue_symbol or settings.venue_symbol
    canonical_symbol = args.canonical_symbol or settings.canonical_symbol
    if not canonical_symbol:
        raise SystemExit("--canonical-symbol or CANONICAL_SYMBOL is required for provider-specific dumps")
    data_dir = args.data_dir or settings.archive_root
    source_path = materialize_source(args.source, data_dir / "downloads")
    source_id = args.source_id or str(
        uuid5(NAMESPACE_URL, f"x2:binance:public-historical:{venue_symbol}")
    )
    database_url = args.database_url or settings.database_url
    if not database_url and not args.no_database:
        raise SystemExit("DATABASE_URL or --database-url is required; use --no-database explicitly for archive-only mode")
    adapter = BinancePublicTradeAdapter(venue_symbol)
    repository = NullRepository()
    postgres_repository = None
    if database_url and not args.no_database:
        postgres_repository = PostgresIngestionRepository(
            database_url,
            venue_id=adapter.venue_id,
            venue_symbol=venue_symbol,
            canonical_symbol=canonical_symbol,
        )
        repository = postgres_repository

    importer = HistoricalDumpImporter(
        adapter=adapter,
        canonical_symbol=canonical_symbol,
        raw_store=RawObjectStore(data_dir / "raw"),
        normalized_writer=ParquetNormalizedWriter(data_dir / "normalized"),
        repository=repository,
    )
    try:
        result = importer.import_file(source_path, source_id=source_id)
    finally:
        if postgres_repository is not None:
            postgres_repository.close()
    output = asdict(result)
    output["raw_object_path"] = str(result.raw_object_path)
    output["normalized_paths"] = [str(path) for path in result.normalized_paths]
    output["quality_findings"] = [asdict(finding) for finding in result.quality_findings]
    for finding in output["quality_findings"]:
        if finding["event_time"] is not None:
            finding["event_time"] = finding["event_time"].isoformat()
    print(json.dumps(output, default=str, indent=2))
    return 0


def _load_fixtures(settings: Settings) -> int:
    import csv

    fixture_dir = settings.archive_root / "fixtures" / "multi_asset" / "5-day-2026-11-20-to-2026-11-24"
    manifest_path = fixture_dir / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"fixture manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    expected_categories = {"eq", "etf", "index", "gold", "oil", "crypto"}
    symbols = manifest.get("symbols")
    if not isinstance(symbols, dict):
        raise SystemExit("fixture manifest symbols must be an object")
    categories = {details["category"] for details in symbols.values()}
    if categories != expected_categories:
        raise SystemExit(
            f"fixture categories mismatch: {sorted(categories)} != {sorted(expected_categories)}"
        )

    header = ["bar_start", "bar_end", "open", "high", "low", "close", "volume", "trade_count"]
    summary: dict[str, dict[str, int]] = {}
    bars_dir = fixture_dir / "bars"
    for symbol, details in symbols.items():
        safe_symbol = symbol.replace("=", "_").replace("/", "_")
        symbol_summary: dict[str, int] = {}
        for interval in ("1m", "5m", "15m"):
            path = bars_dir / f"{safe_symbol}-{interval}.csv"
            if not path.is_file():
                raise SystemExit(f"fixture bar file not found: {path}")
            with path.open("r", encoding="utf-8", newline="") as source:
                reader = csv.reader(source)
                if next(reader, None) != header:
                    raise SystemExit(f"unexpected fixture header: {path}")
                count = sum(1 for row in reader if row)
            if count != details["bar_counts"][interval]:
                raise SystemExit(
                    f"fixture row count mismatch: {path}: {count} != {details['bar_counts'][interval]}"
                )
            symbol_summary[interval] = count
        summary[symbol] = symbol_summary

    print(json.dumps({"fixture": manifest["fixture"], "assets": summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
