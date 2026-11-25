# Confluence_v1 — Multi-Market Intraday System (IBKR + SCB)

## Overview
Deterministic intraday trading and signal-generation architecture supporting **17+ global exchanges/markets** via **Interactive Brokers (IBKR)** and **Standard Chartered Bank (SCB)**, starting from a **$25,000 USD** daily-kitty-scaled base account.

## Key Operating Rules
- **Two-Stage Discovery & Evaluation:** 30-minute slot-normalized `RVOL` discovery across pre-seeded `core_symbols` + volume movers -> continuous 5-minute streaming signal evaluation.
- **Regime Discipline:**
  - `NEWS_DAY`: Zero new entries.
  - `CHOP_DAY`: Max `0.08%` risk per trade of daily start kitty with strict VWAP/Fib filtering.
  - `TREND_DAY`: Full conviction sizing (`0.25%` A-tier core Fib, `0.17%` standard/tolerance; `0.15%` probationary `NVDA`).
- **Symbol & Market Tiering:**
  - `SLV` removed.
  - `NVDA` on probation (`PF > 1.25` required).
  - Core capital allocation: `35%` US Equities/ETFs, `20%` Europe, `20%` Commodities, `15%` Asia-Pacific, `10%` India.
  - Probation buckets: Crypto, Germany (`DAX`), Sweden (`OMXS30`).
  - Disabled until `PF > 1.10`: Hong Kong (`HKEX`).
- **Risk & Execution Guardrails:**
  - Daily-kitty-scaled per-trade risk, soft/hard session stops, correlation cluster caps, `>= 60`-minute pre-close entry cutoff, and mandatory pre-close flattening (24/7 crypto uses rolling session window rules).

## Data Ingestion

- [Data ingestion architecture](docs/data-ingestion-architecture.md) — local-first historical and live tick ingestion design.
- [Local-first implementation plan](docs/local-first-implementation-plan.md) — target architecture, local runtime, data lifecycle, strategy/backtest boundaries, and acceptance criteria.
- [Implementation TODO](TODO.md) — phased checklist for building the local system before cloud hosting or live routing.
- [Market-data schema](schemas/market_data.sql) — PostgreSQL schema for venues, instruments, aliases, ingestion runs, trades, quotes, bars, feed events, and quality events.
- **Multi-asset foundation:** EQ/ETF, Metal, FX, Crypto, futures, options, bonds, and indices use the same provenance and canonical event model. Provider aliases and news/RSS events are persisted separately from trade ticks.

## Local Phase 0–3 setup

The first executable slice is a Binance public historical-trade importer for `BTCUSDT`. It stores native source files under `data/raw/`, normalized Parquet under `data/normalized/`, and optionally writes normalized trades and provenance to PostgreSQL. It does not connect to a broker or place orders.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[runtime,dev]'
python -m unittest discover -s tests -v
```

With Docker Desktop running, start PostgreSQL and apply all migrations:

```bash
./scripts/docker-up.sh
```

Run migrations and unit tests in containers:

```bash
./scripts/docker-test.sh
```

Open an application shell or reset the local database:

```bash
./scripts/docker-shell.sh
./scripts/docker-reset.sh
```

Import a local public dump without database writes while developing the parser:

```bash
python -m app.cli import-dump /path/to/BTCUSDT-trades.zip --no-database
```

An allow-listed HTTPS Binance historical-dump URL can be supplied instead of a local path. For a PostgreSQL-backed import, set `DATABASE_URL` to the restricted `x2_ingestion` role and omit `--no-database`. Public data terms and the source file checksum must be verified before storing or redistributing downloaded data.

### Provider status

- **Binance:** implemented historical trade-dump adapter and WebSocket payload parser for `BTCUSDT`; Redistributing public dumps still requires verified terms.
- **Yahoo Finance:** source endpoint registered for future OHLC adapters. No live or historical ticker ingestion is enabled automatically.
- **Google Finance:** source endpoint registered for future reference/news adapters. Its terms and attribution requirements remain unverified.
- **Stooq:** source endpoint registered for future historical dump adapters. Access behavior and redistribution terms remain unverified.
- **RSS/news:** generic `feed_events` storage is available; feed parsing and deduplication are not yet implemented.
