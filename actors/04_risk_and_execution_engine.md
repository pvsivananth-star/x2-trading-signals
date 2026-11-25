# Actor 04: Risk & Pluggable Execution Engine

## Risk Controls
- Base account seed: `$25,000 USD` with daily-kitty scaling.
- Sleeve weights: `35%` US Equities/ETFs, `20%` Europe, `20%` Commodities, `15%` Asia-Pacific, `10%` India.
- Correlation cluster caps prevent over-stacking correlated symbols in the same session/cluster.
- Soft/hard daily loss stops, drawdown throttles, `>= 60`-minute pre-close entry cutoff, and mandatory pre-close flattening.

## Pluggable Broker Adapter (`mode: "demo"` -> `"live"`)
- Currently operates in `demo` mode: emits explainable entry/exit decision cards and records dummy trades for the dashboard.
- Routes via market profile (`IBKR` or `SCB`) when switched to `live` mode without altering strategy logic.
