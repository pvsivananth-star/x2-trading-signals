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
- [Market-data schema](schemas/market_data.sql) — PostgreSQL schema for venues, instruments, ingestion runs, trades, quotes, bars, and quality events.
