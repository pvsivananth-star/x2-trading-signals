# Actor 02: Regime & Two-Stage Scanner Engine

## 1. Point-in-Time Regime Classification
- Classifies each active market session into `TREND_DAY`, `CHOP_DAY`, or `NEWS_DAY` using causal/intraday data without hindsight.
- `NEWS_DAY`: Blocks all new entries.
- `CHOP_DAY`: Caps per-trade risk at `0.08%` of daily start kitty with strict VWAP/Fib filtering.

## 2. Two-Stage Discovery -> Streaming Pipeline
- **Stage 1 (Every 30 Mins):** Pulls open-API intraday data for `core_symbols` + available volume movers in currently open markets (excluding first 15m, lunch breaks, and final 60m before close). Filters by `slot_RVOL >= slot_rvol_min` and excludes `NEWS_DAY` / gap-exhausted names.
- **Stage 2 (Every 5 Mins):** Streams active candidates to detect `Confluence_v1` reaction-zone entries and trendline-break exits.
