# BTCUSDT five-day replay fixture

- Range: 2026-11-20 through 2026-11-24 UTC
- Source: Binance public spot daily trade dumps
- Parsed trade rows: 26,239,494
- Bars: 1-minute (7,200), 5-minute (1,440), 15-minute (480)
- Raw ZIPs: retained locally under `raw/`; excluded from Git by repository ignore rules
- Checksums: `SHA256SUMS`
- Terms status: unverified; redistribution remains disabled until reviewed

Rebuild with:

```bash
./scripts/download_binance_five_days.sh
python3 scripts/build_five_day_bars_streaming.py
```

Backtests use the deterministic CSV bars and `app/backtest/simulator.py`. External AI services such as Gemini are advisory only: they cannot alter bar inputs, fill prices, risk parameters, or order execution.
