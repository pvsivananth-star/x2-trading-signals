"""Command-line entry points for local ingestion."""

from __future__ import annotations

import argparse
import json
import os
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

    tick_pump = subparsers.add_parser("tick-pump", help="backtest 25%% warmup then pump fixture ticks once per second")
    tick_pump.add_argument("--config", type=Path, default=Path("config/feeds.yaml"))
    tick_pump.add_argument("--ticks", type=int, default=None)
    tick_pump.add_argument("--interval", type=float, default=1.0)
    tick_pump.add_argument("--warmup", type=float, default=0.25)

    audited = subparsers.add_parser("backtest-report", help="produce an audited 252-session multi-market summary")
    audited.add_argument("--config", type=Path, default=Path("config/feeds.yaml"))
    audited.add_argument("--days", type=int, default=252)
    audited.add_argument("--ticks-per-day", type=int, default=20)
    audited.add_argument("--interval", type=float, default=1.0,
                         help="real seconds to sleep per replay tick; 0 disables timing")

    dataset = subparsers.add_parser(
        "backtest-dataset",
        help="validate and run the strategy on a local bars folder (bars.csv, instruments.csv)",
    )
    dataset.add_argument("--dir", type=Path, required=True,
                         help="folder with bars.csv and instruments.csv (synthetic fixture or local licensed copy)")
    dataset.add_argument("--interval", type=int, default=300, help="bar interval in seconds (60, 300, 900)")
    dataset.add_argument("--start-equity", type=str, default="25000", help="starting equity per symbol")
    dataset.add_argument("--risk-pct", type=str, default="0.0025", help="equity risk per trade (A-tier cap)")
    dataset.add_argument("--allow-estimates", action="store_true",
                         help="apply unverified cost estimates (slippage); off by default")
    dataset.add_argument("--validate-only", action="store_true",
                         help="validate the dataset and do not execute the strategy")
    dataset.add_argument("--fail-on-warnings", action="store_true",
                         help="treat dataset warnings such as session gaps as critical")
    dataset.add_argument("--synthetic-warmup-sessions", type=int, default=0,
                         help="exclude the first N local sessions from scored decisions while retaining them for indicator warmup")
    dataset.add_argument("--min-scored-sessions", type=int, default=20,
                         help="minimum remaining local sessions required per instrument after warmup (default: 20)")
    dataset.add_argument("--dump-synthetic-warmup", type=Path, default=None,
                         help="materialize the first N derived warmup sessions into this directory")
    dataset.add_argument("--out", type=Path, default=Path("backtest_runs/latest"))

    start_api = subparsers.add_parser("start-api", help="start the localhost read-only HTTP API")
    start_api.add_argument("--host", default="127.0.0.1")
    start_api.add_argument("--port", type=int, default=8080)
    start_api.add_argument("--config", type=Path, default=Path("config/feeds.yaml"))
    start_api.add_argument("--with-feeds", action="store_true", help="start feed and actor runtime")

    subparsers.add_parser("start-feed", help="start one-shot feed and actor refresh; use start-api --with-feeds for runtime")

    mock_broker = subparsers.add_parser("start-mock-broker", help="start the loopback-only demo/mock broker server")
    mock_broker.add_argument("--host", default=None)
    mock_broker.add_argument("--port", type=int, default=None)
    mock_broker.add_argument("--delay", "--delay-ms", dest="delay_ms", type=int, default=None)
    mock_broker.add_argument("--success", "--success-rate", dest="success_rate", type=float, default=None)

    status = subparsers.add_parser("status", help="show configured feed status without network access")
    status.add_argument("--config", type=Path, default=Path("config/feeds.yaml"))
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
        if args.command == "tick-pump":
            os.environ["TRADING_MODE"] = "backtest"
            from decimal import Decimal

            from app.actors import ActorRuntime
            from app.backtest.tick_pump import TickPumpRuntime
            from app.execution import DemoBroker
            from app.feeds import FeedStore, load_feed_config
            from app.logging_config import configure_logging
            from app.policy import TradingPolicy
            from app.strategy import ConfluenceV1

            configure_logging()
            config = load_feed_config(args.config)
            store = FeedStore(config)
            policy = TradingPolicy.from_yaml("config/markets_17.yaml")
            strategy = ConfluenceV1.from_yaml("config/strategies/confluence_pullback.yaml", policy)
            actor = ActorRuntime(store, policy, strategy, interval="1m",
                                 stale_threshold_seconds=config.stale_threshold_seconds)
            broker = DemoBroker(policy, starting_cash=Decimal(25000))
            pump = TickPumpRuntime(store, actor, broker, warmup_fraction=args.warmup,
                                   interval_seconds=args.interval)
            result = pump.run(max_ticks=args.ticks)
            print(json.dumps({
                "status": "completed", "mode": "backtest",
                "ticks_processed": result.ticks_processed,
                "warmup_ticks": result.warmup_ticks,
                "decision_count": len(result.decisions),
                "execution_audit_count": len(result.audit),
                "portfolio": {"cash": str(broker.cash), "positions": len(broker.positions),
                              "fills": len(broker.fills), "orders": len(broker.orders)},
            }, indent=2))
            return 0

        if args.command == "backtest-dataset":
            from decimal import Decimal

            from app.backtest.dataset_runner import (
                DEFAULT_PROFILE,
                dump_synthetic_warmup,
                inspect_scored_data,
                run_dataset,
                write_outputs,
            )
            from app.backtest.validation import validate_dataset

            if args.synthetic_warmup_sessions < 0:
                raise SystemExit("--synthetic-warmup-sessions cannot be negative")
            if args.min_scored_sessions < 1:
                raise SystemExit("--min-scored-sessions must be at least 1")

            validation = validate_dataset(
                args.dir,
                interval_seconds=args.interval,
                profile_map=DEFAULT_PROFILE,
                require_profile=True,
                fail_on_warnings=args.fail_on_warnings,
            )
            validation_payload = validation.as_dict()
            print(json.dumps({"dataset_validation": validation_payload}, indent=2))
            if not validation.valid:
                args.out.mkdir(parents=True, exist_ok=True)
                (args.out / "dataset_validation.json").write_text(
                    json.dumps(validation_payload, indent=2) + "\n", encoding="utf-8"
                )
                print("dataset validation failed; backtest not executed", file=sys.stderr)
                return 2

            if args.validate_only:
                args.out.mkdir(parents=True, exist_ok=True)
                (args.out / "dataset_validation.json").write_text(
                    json.dumps(validation_payload, indent=2) + "\n", encoding="utf-8"
                )
                return 0

            try:
                scored_data_coverage = inspect_scored_data(
                    args.dir, interval_seconds=args.interval,
                    warmup_sessions=args.synthetic_warmup_sessions,
                    min_scored_sessions=args.min_scored_sessions, profile_map=DEFAULT_PROFILE,
                )
            except ValueError as exc:
                print(json.dumps({
                    "status": "blocked", "reason": "INSUFFICIENT_SCORED_DATA",
                    "minimum_scored_sessions": args.min_scored_sessions,
                    "warmup_sessions_requested": args.synthetic_warmup_sessions,
                    "error": str(exc),
                }, indent=2), file=sys.stderr)
                return 2

            warmup_dump = None
            if args.dump_synthetic_warmup is not None:
                if args.synthetic_warmup_sessions <= 0:
                    raise SystemExit("--dump-synthetic-warmup requires --synthetic-warmup-sessions > 0")
                warmup_dump = dump_synthetic_warmup(
                    args.dir,
                    args.dump_synthetic_warmup,
                    interval_seconds=args.interval,
                    warmup_sessions=args.synthetic_warmup_sessions,
                    profile_map=DEFAULT_PROFILE,
                )

            result = run_dataset(
                args.dir,
                interval_seconds=args.interval,
                start_equity=Decimal(args.start_equity),
                risk_pct=Decimal(args.risk_pct),
                allow_estimates=args.allow_estimates,
                warmup_sessions=args.synthetic_warmup_sessions,
                min_scored_sessions=args.min_scored_sessions,
            )
            result["scored_data_coverage"] = scored_data_coverage
            result["min_scored_sessions"] = args.min_scored_sessions
            result["dataset_validation"] = validation_payload
            result["warmup_sessions_requested"] = args.synthetic_warmup_sessions
            result["warmup_dump"] = warmup_dump
            result["warmup_sessions_applied"] = max(
                (v.get("warmup_sessions", 0) for v in result.get("per_symbol", {}).values()),
                default=0,
            )
            result["warmup_rows"] = (
                warmup_dump["warmup_rows"] if warmup_dump is not None
                else validation_payload.get("warmup_rows", 0)
            )
            if args.synthetic_warmup_sessions:
                result["warmup_dates"] = sorted({
                    d for value in result.get("per_symbol", {}).values()
                    for d in value.get("warmup_dates", [])
                })
                result["first_scored_session"] = min(
                    (
                        value["first_scored_session"]
                        for value in result.get("per_symbol", {}).values()
                        if value.get("first_scored_session") is not None
                    ),
                    default=None,
                )
            write_outputs(result, args.out)
            printable = {k: v for k, v in result.items() if k != "_trades"}
            print(json.dumps(printable, indent=2, default=str))
            print(f"ledger and summary written to {args.out}", file=sys.stderr)
            return 0

        if args.command == "backtest-report":
            os.environ["TRADING_MODE"] = "backtest"
            from decimal import Decimal

            from app.actors import ActorRuntime
            from app.backtest.report import run_audited_backtest
            from app.execution import DemoBroker
            from app.feeds import FeedStore, load_feed_config
            from app.logging_config import configure_logging
            from app.policy import TradingPolicy
            from app.strategy import ConfluenceV1

            configure_logging()
            config = load_feed_config(args.config)
            store = FeedStore(config)
            policy = TradingPolicy.from_yaml("config/markets_17.yaml")
            strategy = ConfluenceV1.from_yaml("config/strategies/confluence_pullback.yaml", policy)
            actor = ActorRuntime(store, policy, strategy, interval="1m",
                                 stale_threshold_seconds=config.stale_threshold_seconds)
            broker = DemoBroker(policy, starting_cash=Decimal(25000))
            report = run_audited_backtest(store, actor, broker, days=args.days,
                                          ticks_per_day=args.ticks_per_day,
                                          interval_seconds=args.interval)
            print(json.dumps({**report.as_table(), "BUY signals": report.buy_signals,
                              "SELL signals": report.sell_signals,
                              "HOLD signals": report.held_signals,
                              "Audit warning": report.audit_warning}, indent=2))
            return 0

        if args.command == "start-mock-broker":
            from app.mock_broker import MockBroker, MockBrokerServer
            env_broker = MockBroker.from_env()
            broker = MockBroker(
                host=args.host or env_broker.host,
                port=args.port if args.port is not None else env_broker.port,
                delay_ms=args.delay_ms if args.delay_ms is not None else env_broker.delay_ms,
                success_rate=args.success_rate if args.success_rate is not None else env_broker.success_rate,
            )
            server = MockBrokerServer(broker)
            print(json.dumps({"status": "started", "mode": "demo", "host": broker.host,
                              "port": broker.port, "delay_ms": broker.delay_ms,
                              "success_rate": broker.success_rate, "live_write_enabled": False}))
            try:
                server.serve_forever()
            finally:
                server.shutdown()
            return 0

        if args.command in {"start-api", "start-feed", "status"}:
            from app.feeds import FeedRuntime, FeedStore, load_feed_config
            from app.logging_config import configure_logging
            configure_logging()
            config_path = getattr(args, "config", Path("config/feeds.yaml"))
            feed_config = load_feed_config(config_path)
            store = FeedStore(feed_config)
            if args.command == "status":
                print(json.dumps({"symbols": store.market_symbols(), "live_write_enabled": False,
                                  "fallback": str(feed_config.fallback_fixture)}, indent=2))
                return 0
            if args.command == "start-feed":
                runtime = FeedRuntime(store, feed_config)
                runtime.run_once()
                return 0
            from app.api import run_server
            run_server(store, feed_config, host=args.host, port=args.port, with_feeds=args.with_feeds)
            return 0

        raise SystemExit(f"unknown command: {args.command}")
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

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
