# Cost review — profit margin basis

Any change to a verified cost value, or to the threshold it feeds, needs a second review.

## Worked example: 100 shares of SPY at $524 (notional $52,400)

| Component | Round trip $ | % of notional | Status |
|---|---|---|---|
| Commission (IBKR Pro Tiered, minimum applies) | 0.70 | 0.0013% | verified |
| SEC fee (sell side) | 1.08 | 0.0021% | verified |
| FINRA TAF (sell side), CAT, NSCC/DTC | 0.06 | 0.0001% | verified |
| Spread (SPY, 0.27 bps) | 1.41 | 0.0027% | verified (SSGA, June 2024) |
| **Verified subtotal** | **3.25** | **0.0062%** | |
| Slippage (1 bp per side) | 10.48 | 0.0200% | estimate, unverified |
| Exchange fee | not found | — | blocks live use |
| **Total incl. estimate** | **13.73** | **0.0262%** | |

The config placeholder is 0.15% (0.10% cost + 0.05% slippage), about six times the total above. Replace it only after the exchange fee and measured slippage are sourced.

## Required gross move

Net MoveRoom threshold: 0.45%. Required gross ≈ 0.45% + 0.03% ≈ 0.48%. With the 1× ATR stop at about 0.115% of price, a 2R target needs about 0.23%.

## Daily arithmetic (illustrative, not a forecast)

Assumes 0.25% equity risk per trade (A-tier cap), 2R target, 1× ATR stop, and costs above. Sizing implies roughly 2.2× equity notional, so margin is required.

| Win rate | Expectancy per trade (% of equity) | Trades needed for 2% day |
|---|---|---|
| 50% | 0.07% | about 29 |
| 55% | 0.11% | about 19 |
| 60% | 0.14% | about 14 |

Expectancy is positive only above a win rate of roughly 45–50% under these assumptions. The 2% day needs either a high win rate or many trades, and the strategy has not been measured to show either.

## Open items before live use

1. Exchange fee per venue (US equities).
2. Measured spread and slippage per instrument from quote and fill data.
3. Futures exchange recovery for CL, GC, MCL, MGC, MES.
4. Spot crypto commission and spread.
5. Non-US markets: HKEX, NSE.
