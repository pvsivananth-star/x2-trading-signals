# X2 Trading System

X2 is a local-first, provider-neutral, multi-market intraday trading platform. The target is a **modular monolith first**, event-driven internally, container-ready, and separable into services later when scale requires it.

## Source of truth

The current architecture and requirements are defined by:

- `requirements.md` — functional, trading, risk, execution, simulation, UI and operational requirements.
- `architecture.md` — domain boundaries, actors, ports/adapters, events, state machines, persistence, observability, security and deployment architecture.
- `config/` — strategy and market configuration.
- `tests/` — executable behavioral contracts.

Older planning documents under `docs/` and historical implementation notes must not override these two specification files.

## Current baseline

The repository already contains a substantial research/backtest foundation inherited from `main`, including:

- canonical market-data contracts and ingestion/replay foundations;
- deterministic OHLCV fixtures and causal feature calculations;
- multi-timeframe trend, VWAP, RVOL, Fibonacci, structure and MoveRoom logic;
- the existing `Confluence_v1` strategy implementation;
- deterministic dataset/tick backtesting and metrics;
- demo/mock execution and accounting;
- risk/policy gates and fail-closed live-routing behavior;
- read-only API/dashboard foundations;
- local PostgreSQL/Docker support;
- configuration for markets, feeds and strategy parameters.

This baseline is **not yet the final architecture** described by `architecture.md`. The next implementation work should refactor the existing behavior behind the specified domain/application boundaries rather than continue adding isolated features to the current structure.

## Safety

Live execution remains disabled by default. The system must prefer:

**NO_TRADE > BAD_TRADE**

No strategy, analyzer, UI or connector may bypass portfolio/risk controls.

## Local development

Python 3.12+ is required.

Create an environment and install development dependencies:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[runtime,dev]'
```

Run tests:

```python -m unittest discover -s tests -v
```

The local Docker stack and existing CLI commands remain available while the architecture refactor is performed.

## Architecture direction

The target flow is:

```text
Market Data
    -> Normalization / Clock / Calendar
    -> Event Bus
    -> Market / Regime / Feature Actors
    -> Signal Manager
    -> Strategy Manager
    -> Trade Manager
    -> Portfolio Controller <-> Risk Manager
    -> Execution Manager
    -> Broker / Exchange Adapter

Backtest / Replay
    -> same domain/application logic
    -> simulated ExecutionPort

UI / API
    -> read models / audit events
    -> explicit, separately authorized controls
```

Vendor SDKs, HTTP servers, databases and filesystem details belong behind ports/adapters. Domain logic must not depend on them.

## Implementation rule

Do not create microservices prematurely. First establish strong module boundaries, deterministic contracts, state machines, idempotency, auditability and tests inside the modular monolith. Containers and service extraction come after local acceptance.



## Free historical research data

The repository includes a no-key downloader for recent 5-minute OHLCV data:

```bash
# First symbol creates the dataset
python -m app.cli download-free-data \
  --symbol SPY \
  --days 59 \
  --out ../back-test-copyright/free-market-data

# Download further symbols one at a time into the same dataset
python -m app.cli download-free-data \
  --symbol NVDA \
  --days 59 \
  --append \
  --out ../back-test-copyright/free-market-data
```

It downloads the configured 16-symbol multi-market universe into `bars.csv`,
`instruments.csv`, `manifest.json` and `download_failures.csv`. The downloader
refuses to overwrite a non-empty output directory and never fills missing bars.
Yahoo Finance intraday history is limited to the most recent 60 calendar days. The downloader defaults to 59 days to avoid edge-of-window HTTP 422 responses, tries both Yahoo chart hosts, and supports one-symbol-at-a-time append runs.
The public `yfinance` documentation describes this access as intended for
personal use; check the provider's terms before use and do not redistribute
downloaded data without permission.

Validate and run the backtest:

```bash
python -m app.cli backtest-dataset \
  --dir ../back-test-copyright/free-market-data \
  --interval 300 \
  --synthetic-warmup-sessions 15 \
  --out backtest_runs/free-market-data
```

This free source is suitable for pipeline and short-window strategy research,
not consolidated-volume or bid/ask execution validation. It does not replace
multi-year historical data for robust out-of-sample profitability analysis.
