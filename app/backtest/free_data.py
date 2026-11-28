"""Free historical OHLCV acquisition for local research datasets.

Yahoo Finance's public chart endpoint is used without credentials. Its intraday
retention is limited; this adapter deliberately fails rather than inventing bars.
Check the provider's terms before use; downloaded data is for personal research
and must not be redistributed without permission.
"""
from __future__ import annotations

import csv
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


DEFAULT_SYMBOLS = (
    "SPY", "NVDA", "GLD", "USO", "BTC-USD", "ETH-USD",
    "NIFTYBEES.NS", "RELIANCE.NS", "XIU.TO", "ISF.L", "SAP.DE",
    "ASML.AS", "1306.T", "D05.SI", "BHP.AX", "005930.KS",
)
INTERVAL_SECONDS = 300
MAX_INTRADAY_DAYS = 60
BAR_FIELDS = (
    "instrument_id", "interval_seconds", "bar_start", "bar_end",
    "open_price", "high_price", "low_price", "close_price", "volume",
    "trade_count", "is_complete", "quality_status", "source",
)


class FreeDataDownloadError(RuntimeError):
    """Raised when a requested download cannot be completed safely."""


def _fetch_symbol(symbol: str, start: datetime, end: datetime, timeout: float) -> tuple[list[dict[str, str]], str]:
    params = urlencode({
        "period1": int(start.timestamp()),
        "period2": int(end.timestamp()),
        "interval": "5m",
        "includePrePost": "false",
        "events": "div,splits",
    })
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}?{params}"
    request = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; X2ResearchData/1.0)",
        "Accept": "application/json",
    })
    last_error: Exception | None = None
    payload = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    if payload is None:
        raise FreeDataDownloadError(f"{symbol}: download failed: {last_error}")
    chart = payload.get("chart", {})
    if chart.get("error"):
        raise FreeDataDownloadError(f"{symbol}: provider error: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise FreeDataDownloadError(f"{symbol}: provider returned no history")
    result = results[0]
    timestamps = result.get("timestamp") or []
    quote_data = (result.get("indicators", {}).get("quote") or [{}])[0]
    rows: list[dict[str, str]] = []
    missing = 0
    now = datetime.now(UTC)
    for index, stamp in enumerate(timestamps):
        try:
            values = [quote_data[name][index] for name in ("open", "high", "low", "close", "volume")]
            if any(value is None for value in values[:4]):
                continue
            start_at = datetime.fromtimestamp(int(stamp), UTC)
            end_at = start_at + timedelta(seconds=INTERVAL_SECONDS)
            if end_at > now:
                continue
            open_, high, low, close = (float(value) for value in values[:4])
            volume = values[4]
            if volume is None:
                missing += 1
                volume = 0
            if min(open_, high, low, close) <= 0 or high < max(open_, close) or low > min(open_, close):
                continue
            if float(volume) < 0:
                continue
            rows.append({
                "bar_start": start_at.isoformat(),
                "bar_end": end_at.isoformat(),
                "open_price": format(open_, ".12g"),
                "high_price": format(high, ".12g"),
                "low_price": format(low, ".12g"),
                "close_price": format(close, ".12g"),
                "volume": str(int(volume)),
                "trade_count": "0",
                "quality_status": "ok" if not missing else "volume_missing_from_source",
            })
        except (IndexError, KeyError, TypeError, ValueError, OverflowError):
            continue
    rows.sort(key=lambda row: row["bar_start"])
    if not rows:
        raise FreeDataDownloadError(f"{symbol}: provider returned no usable completed 5-minute bars")
    timezone = str(result.get("meta", {}).get("exchangeTimezoneName") or "unknown")
    return rows, timezone


def download_free_dataset(
    output_dir: str | Path,
    *,
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    days: int = MAX_INTRADAY_DAYS,
    profile_map: dict[str, str] | None = None,
    timeout: float = 20.0,
) -> dict[str, object]:
    """Download real 5-minute bars and write the backtest's canonical CSV layout."""
    if not 1 <= days <= MAX_INTRADAY_DAYS:
        raise ValueError(f"Yahoo 5-minute history is limited to {MAX_INTRADAY_DAYS} calendar days")
    if not symbols or len(set(symbols)) != len(symbols):
        raise ValueError("symbols must be non-empty and unique")
    profiles = profile_map or {}
    unknown = sorted(set(symbols) - set(profiles))
    if unknown:
        raise ValueError(f"symbols missing backtest profiles: {unknown}")
    output = Path(output_dir).expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"refusing to overwrite non-empty dataset directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    start = datetime.now(UTC) - timedelta(days=days)
    end = datetime.now(UTC)
    bars_path = output / "bars.csv"
    instruments_path = output / "instruments.csv"
    failures: list[dict[str, str]] = []
    coverage: list[dict[str, object]] = []
    instrument_rows: list[dict[str, str]] = []
    total_rows = 0

    with bars_path.open("w", newline="", encoding="utf-8") as bars_handle:
        writer = csv.DictWriter(bars_handle, fieldnames=BAR_FIELDS)
        writer.writeheader()
        for instrument_id, symbol in enumerate(symbols, start=1):
            try:
                rows, source_timezone = _fetch_symbol(symbol, start, end, timeout)
                for row in rows:
                    writer.writerow({
                        "instrument_id": instrument_id,
                        "interval_seconds": INTERVAL_SECONDS,
                        **row,
                        "is_complete": "true",
                        "source": "yahoo_finance_chart",
                    })
                total_rows += len(rows)
                instrument_rows.append({
                    "instrument_id": str(instrument_id),
                    "canonical_symbol": symbol,
                    "profile": profiles[symbol],
                    "market": "US" if profiles[symbol] == "US_EQUITIES" else "OTHER",
                    "tradable": "true",
                    "source": "yahoo_finance_chart",
                })
                coverage.append({
                    "symbol": symbol, "rows": len(rows),
                    "first_bar": rows[0]["bar_start"], "last_bar": rows[-1]["bar_start"],
                    "exchange_timezone": source_timezone,
                })
            except (FreeDataDownloadError, OSError, ValueError) as exc:
                failures.append({"symbol": symbol, "error": str(exc)})
            time.sleep(1.0)

    with instruments_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=(
            "instrument_id", "canonical_symbol", "profile", "market", "tradable", "source",
        ))
        writer.writeheader()
        writer.writerows(instrument_rows)

    manifest = {
        "provider": "Yahoo Finance public chart endpoint",
        "source_url": "https://query1.finance.yahoo.com/v8/finance/chart/",
        "downloaded_at_utc": datetime.now(UTC).isoformat(),
        "requested_days": days,
        "interval_seconds": INTERVAL_SECONDS,
        "source_interval": "5m",
        "adjustment_mode": "raw",
        "prepost": False,
        "symbols_requested": list(symbols),
        "symbols_downloaded": [item["symbol"] for item in coverage],
        "coverage": coverage,
        "total_rows": total_rows,
        "failed_symbols": failures,
        "limitations": [
            "Intraday history is limited to the provider's recent retention window (maximum 60 calendar days).",
            "OHLCV does not provide consolidated volume, bid/ask quotes, or trade counts.",
            "Missing source volume is recorded as zero and flagged; it is not inferred.",
            "Data is unadjusted raw OHLCV; review corporate actions before long-horizon use.",
            "Review Yahoo Finance terms; yfinance documents this access as intended for personal use.",
        ],
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    with (output / "download_failures.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("symbol", "error"))
        writer.writeheader()
        writer.writerows(failures)
    return {
        "output_dir": str(output), "symbols_requested": len(symbols),
        "symbols_downloaded": len(coverage), "symbols_failed": len(failures),
        "rows": total_rows, "interval_seconds": INTERVAL_SECONDS,
        "days_requested": days, "failures": failures,
    }
