# Actor 06: Trade Analyzer & Rectifier

## Post-Trade & Periodic Audit
- Tracks monthly/quarterly/out-of-sample trade count, win rate, profit factor, max drawdown, and consecutive-loss streaks.
- Enforces symbol and market probation gates (`NVDA` `PF > 1.25`; `HKEX` disabled until `PF > 1.10`; `SLV` excluded).
- Logs every decision audit card (entry zone, `slot_RVOL`, net `MoveRoom`, regime, cluster exposure, and exit reason/slippage).
