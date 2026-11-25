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
- [Market-data schema](schemas/market_data.sql) — PostgreSQL schema for venues, instruments, ingestion runs, trades, quotes, bars, and quality events.

## Local Phase 0–3 setup

The first executable slice is a Binance public historical-trade importer for `BTCUSDT`. It stores native source files under `data/raw/`, normalized Parquet under `data/normalized/`, and optionally writes normalized trades and provenance to PostgreSQL. It does not connect to a broker or place orders.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[runtime,dev]'
python -m unittest discover -s tests -v
```

With Docker installed, start PostgreSQL locally and apply migrations:

```bash
docker compose -f infra/docker-compose.local.yml up -d postgres
export DATABASE_URL=postgresql://x2_admin:x2_admin_local_only@127.0.0.1:5432/x2_trading
python -m app.cli migrate
```

Import a local public dump without database writes while developing the parser:

```bash
python -m app.cli import-dump /path/to/BTCUSDT-trades.zip --no-database
```

An allow-listed HTTPS Binance historical-dump URL can be supplied instead of a local path. For a PostgreSQL-backed import, set `DATABASE_URL` to the restricted `x2_ingestion` role and omit `--no-database`. Public data terms and the source file checksum must be verified before storing or redistributing downloaded data.
