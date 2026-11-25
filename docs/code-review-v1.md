# Code Review v1 — Confluence_v1 vs. strategy and actor specs

## Fixed in this review
- Reaction zones: entries require a located pullback zone (core 38.2–61.8%, shallow 23.6–38.2% with VWAP hold, deep 61.8–78.6% with reclaim close). Entry outside zones is rejected with `outside_reaction_zone`. Missing close or VWAP fails closed.
- Size weights per zone (1.00 / 0.68 / 0.68) are emitted as card parameters for downstream sizing.
- Mandatory pre-close flat is now an exit while long (`MANDATORY_PRE_CLOSE_FLAT`), not only an entry gate.
- Trend-break exits require a confirmed break (current and previous snapshot), per the M=2 confirmation rule.
- Causal pullback depth and last close added to FeatureSnapshot; computed only from bars at or before t.
- Actor runtime evaluates at bar time (`as_of`) instead of wall-clock time, and carries the previous snapshot per symbol.
- Tick-pump stage-2 reaction is now "reaction zone located", not the strategy's own BUY/SELL output (which was circular).
- Decision parameters include zone, size_weight, and max_risk_pct (policy CHOP cap).

## Known gaps (not changed here)
- Swing detection: no 15m timeframe check, no 0.60×ATR minimum swing size, and no 2-bar confirmation in `causal_swing`.
- VWAP is cumulative across the bars supplied, not reset per session.
- RVOL is not slot-normalized to the 30-minute time-of-day profile.
- MoveRoom does not net spread, slippage, and fees.
- No daily loss stop, drawdown throttle, or target/runner exit.
- Correlation cluster caps are not enforced.
- Sizing from `max_risk_pct` and `size_weight` is not applied by execution.

## Verification
- `python -m unittest discover -s tests -v`: 76 tests passing (65 baseline + 11 new).
- `python -m compileall -q app tests`: passing.
- `ruff check app tests`: passing.
- Tick pump on bundled synthetic fixtures: no BUY/SELL fills in 200 ticks. Results are not a performance signal.
