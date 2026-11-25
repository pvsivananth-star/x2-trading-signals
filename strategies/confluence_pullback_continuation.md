# Strategy: Confluence Pullback & Continuation (`Confluence_v1`)

## Timeframes
- **Discovery:** 30-minute slot-normalized `RVOL` puller (outside opening noise, lunch breaks, and final 60m before close).
- **Signal Evaluation:** Continuous 5-minute streaming evaluation on promoted candidates.
- **Swing Anchor:** 15-minute swings (`>= 3` candles, `>= 0.60 * ATR_14(15m)`, confirmed after `M = 2` closed 15m bars to eliminate look-ahead bias).

## Reaction Zones
1. **Core Fib (`38.2% – 61.8%`):** Primary pullback zone aligned with causal intraday VWAP and volume pace.
2. **Shallow VWAP (`23.6% – 38.2%` / VWAP hold):** Calibrated tolerance with reduced risk sizing on trend continuations.
3. **Deep Reclaim (`61.8% – 78.6%`):** Requires confirmed structure/VWAP reclaim after liquidity sweep/wick; reduced risk sizing.

## Net MoveRoom & Exits
- Require `net_moveroom >= market.min_net_moveroom_pct` after spread, slippage, and market-specific taxes/fees.
- Hold intraday while trendline/structure remains intact; exit on confirmed trendline break, target/runner rule, daily stop, or mandatory pre-close flat rule.
