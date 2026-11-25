# X2 Trading Signals — Local-First Multi-Market Research Platform

## Overview

X2 is a provider-neutral, local-first platform for market-data ingestion, replay, backtesting, signal generation, and later demo/paper execution. It is not tied to a specific broker or market.

The data model supports:

- Equities and ETFs
- Foreign exchange
- Metals and commodities
- Crypto
- Futures, options, bonds, and indices
- Provider aliases and RSS/news feed events

This repository is currently a research/CLI foundation. It does not expose a web server or place live orders. Live broker mode is rejected by configuration.

## Repository Guide

- [Implementation TODO](TODO.md) — current implementation and remaining phases
- [Local-first plan](docs/local-first-implementation-plan.md) — architecture and boundaries
- [Data-ingestion architecture](docs/data-ingestion-architecture.md) — providers, provenance, replay, and quality
- [PostgreSQL schema](schemas/market_data.sql) — canonical multi-market schema
- [Confluence strategy configuration](config/strategies/confluence_pullback.yaml)
- [Configured market universe](config/markets_17.yaml)

## Prerequisites

- macOS or Linux
- Python 3.12
- Docker Desktop with the daemon running
- PostgreSQL 16 is provided through Docker Compose

## Start the Local Application

There is currently no HTTP app process or web UI. Start the local database and migration service, then use the CLI commands from the application container or host.

### Docker Desktop setup

1. Start Docker Desktop.
2. From the repository root, run:

```bash
./scripts/docker-up.sh
```

This builds the application image, starts PostgreSQL, applies migrations, and runs the unit tests once. PostgreSQL is exposed only on `127.0.0.1:5432`.

Open an application shell:

```bash
./scripts/docker-shell.sh
```

Inside the container:

```bash
python -m app.cli --help
python -m app.cli migrate
python -m app.cli load-fixtures
```

Reset the local database and reapply a clean migration state:

```bash
./scripts/docker-reset.sh
```

Stop the Docker services:

```bash
docker compose -f infra/docker-compose.local.yml down
```

To remove the database volume too:

```bash
docker compose -f infra/docker-compose.local.yml down -v
```

## Host Development

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[runtime,dev]'
python -m app.cli --help
python -m unittest discover -s tests -v
```

The host can use PostgreSQL by copying `.env.example` to `.env` and setting `DATABASE_URL` to the restricted local ingestion role:

```bash
cp .env.example .env
python -m app.cli migrate
python -m app.cli load-fixtures
```

## Test the Application

Run all unit tests on the host:

```bash
python -m unittest discover -s tests -v
```

Run migrations and tests in containers:

```bash
./scripts/docker-test.sh
```

Run a one-off container command:

```bash
docker compose -f infra/docker-compose.local.yml run --rm app python -m unittest discover -s tests -v
```

## Data Ingestion

The CLI accepts a local market-data dump or an explicitly allow-listed HTTPS source:

```bash
python -m app.cli import-dump /path/to/provider-dump.zip --no-database
```

For PostgreSQL-backed ingestion, configure `DATABASE_URL` and omit `--no-database`:

```bash
python -m app.cli import-dump /path/to/provider-dump.zip
```

Raw source bytes, checksums, ingestion runs, canonical instruments, normalized trades, quality events, and normalized bars use separate provider-neutral contracts. Provider-specific code belongs behind the adapter interface rather than in strategy or replay code.

## Provider and Fixture Status

- **Binance** is an optional sample crypto trade-dump adapter only. It is not required for equity, ETF, index, gold, oil, or provider-neutral support.
- **BTC-USD** is the crypto fixture; **AAPL, SPY, SPX, GC=F, and CL=F** provide the five other required test categories.
- **Yahoo Finance, Google Finance, and Stooq** are registered source definitions; their bulk adapters and term verification remain pending.
- **RSS/news** uses generic feed-event storage. Feed parsing and deduplication remain pending.
- **Open exchange dumps** can be added through provider-neutral historical-dump adapters; no provider or license is assumed automatically.

Redistributed source data must have checked licensing and attribution. Raw fixtures must not contain secrets or broker credentials.

## Replay Fixtures

The local fixture set covers one ticker in every required test category:

- **EQ:** AAPL
- **ETF:** SPY
- **Index:** SPX
- **Gold:** GC=F
- **Oil:** CL=F
- **Crypto:** BTC-USD

All categories contain deterministic, synthetic 1-minute, 5-minute, and 15-minute bars for 2026-11-20 through 2026-11-24 UTC. The bar module also supports 30-minute rollups, UTC-preserving session calendars, empty/gapped and partial-bar semantics. These synthetic files support tests and replay development; they are not real market data and must not be described or used as live or historical observations.

## Collector and Phase 4/5 Foundations

The provider-neutral live collector foundation supports replay, bounded exponential reconnect backoff, terminal failure, in-memory restart checkpoints, event/sequence/venue-ID dedupe, health and lag reporting, stale-feed blocking, and graceful stop/restart. The existing Binance parser supplies trade payload adaptation only; no live WebSocket socket or network transport is implemented.

Quality findings cover required fields, positive values, UTC timestamps, symbol mapping, ordering, dedupe, sequence gaps, and timestamp gaps. Causal VWAP, ATR, RVOL, swing, Fibonacci zones, trend/structure, and net MoveRoom calculations are cutoff at time `t` and exclude bars ending after `t`.

The existing Binance BTCUSDT files remain an optional crypto sample implementation. They are not a system default or required for EQ, ETF, index, gold, oil, or provider-neutral tests.

Validate the fixtures:

```bash
python -m app.cli load-fixtures
```

## Regime, Policy, Replay, and Backtesting

Phases 6 and 7 now provide provider-neutral `NEWS_DAY`, `CHOP_DAY`, and `TREND_DAY` regimes; a fail-closed, versioned policy; and the causal `Confluence_v1` signal engine. Decision cards include gate rejections, Fib zone, MoveRoom, configuration version, deterministic signal ID, and dedupe key. Probationary and disabled markets and live routing remain blocked.

Replay clocks consume normalized in-memory event feeds and existing `Trade`/`Bar` rollups; replay currently reuses the causal feature/bar path, while the Phase-6 strategy card API remains available for callers rather than an internal replay-loop dependency. The replay run exposes chronological and walk-forward splits, market/limit/partial/rejected simulation, spread/slippage/fee configuration, quality gap thresholds, deterministic provenance, and backtest metrics (profit factor, drawdown, win rate, expectancy, streaks, exposure, and turnover). Migration `005_backtest_tables.sql` adds optional PostgreSQL audit storage; unit tests do not require PostgreSQL.

## Backtesting and AI Policy

Backtests use deterministic market-data, bar, and replay code. Gemini or another AI service is advisory only: it may analyze or explain results, but it cannot set prices, alter fills or risk parameters, bypass gates, or place orders.

Live execution is intentionally unavailable until demo/paper accounting, separate authorization, and a broker adapter are implemented and reviewed.
