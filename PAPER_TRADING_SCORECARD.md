# 60–90 Day Live Paper-Trading Scorecard

## Objective
Validate `Confluence_v1` in live market hours across `demo` signal/dummy booking before scaling live capital on **IBKR** and **SCB**.

## Probation & Gate Checks
1. **NVDA Probation:** Must achieve **Profit Factor > 1.25** over the validation/paper window; otherwise remove from active US equities.
2. **Tier C Probation (Crypto, Germany DAX, Sweden OMXS30):** Track separately; require net-of-cost PF `> 1.25` to expand risk weight.
3. **Tier D Disabled (HKEX):** Remains disabled until it demonstrates **PF > 1.10** net of stamp duty, levies, spread, and slippage.
4. **Regime & Execution Discipline:** Zero entries on `NEWS_DAY`; max `0.08%` risk on `CHOP_DAY`; no new entries within `60` minutes of session close; mandatory flat before close.
