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

            from app.backtest.dataset_runner import DEFAULT_PROFILE, dump_synthetic_warmup, run_dataset, write_outputs
            from app.backtest.validation import validate_dataset

            if args.synthetic_warmup_sessions < 0:
                raise SystemExit("--synthetic-warmup-sessions cannot be negative")

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
            )
            result["dataset_validation"] = validation_payload
            result["warmup_sessions_requested"] = args.synthetic_warmup_sessions
            result["warmup_dump"] = warmup_dump
            result["warmup_sessions_applied"] = max(
                (len(v.get("warmup_dates", [])) for v in result.get("per_symbol", {}).values()),
                default=0,
            )
            if args.synthetic_warmup_sessions:
                result["warmup_dates"] = sorted({
                    d for value in result.get("per_symbol", {}).values()
                    for d in value.get("warmup_dates", [])
                })
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


if __name__ == "__main__":
    raise SystemExit(main())
