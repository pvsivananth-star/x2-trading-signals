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
    last_error: Exception | None = None
    payload = None
    for host in ("query2.finance.yahoo.com", "query1.finance.yahoo.com"):
        url = f"https://{host}/v8/finance/chart/{quote(symbol, safe='')}?{params}"
        request = Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "application/json",
        })
        for attempt in range(2):
            try:
                with urlopen(request, timeout=timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
                last_error = FreeDataDownloadError(f"HTTP {exc.code} from {host}: {detail}")
                if exc.code in {429, 500, 502, 503, 504} and attempt == 0:
                    time.sleep(2.0)
                else:
                    break
            except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt == 0:
                    time.sleep(1.5)
        if payload is not None:
            break
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
            volume_missing = volume is None
            if volume_missing:
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
                "quality_status": "volume_missing_from_source" if volume_missing else "ok",
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
    append: bool = False,
) -> dict[str, object]:
    """Download real 5-minute bars and write the backtest's canonical CSV layout.

    With append=True, an existing canonical dataset is loaded and extended.
    A symbol already present in the manifest cannot be downloaded twice.
    """
    if not 1 <= days <= MAX_INTRADAY_DAYS:
        raise ValueError(f"Yahoo 5-minute history is limited to {MAX_INTRADAY_DAYS} calendar days")
    if not symbols or len(set(symbols)) != len(symbols):
        raise ValueError("symbols must be non-empty and unique")

    profiles = profile_map or {}
    unknown = sorted(set(symbols) - set(profiles))
    if unknown:
        raise ValueError(f"symbols missing backtest profiles: {unknown}")

    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    bars_path = output / "bars.csv"
    instruments_path = output / "instruments.csv"
    manifest_path = output / "manifest.json"
    required_paths = (bars_path, instruments_path, manifest_path)
    directory_has_content = any(output.iterdir())

    existing_instruments: list[dict[str, str]] = []
    existing_manifest: dict[str, object] = {}

    if directory_has_content:
        if not append:
            raise FileExistsError(
                f"refusing to overwrite non-empty dataset directory: {output}; use --append"
            )
        missing = [path.name for path in required_paths if not path.is_file()]
        if missing:
            raise FileExistsError(
                f"cannot append: existing dataset is incomplete; missing {', '.join(missing)}"
            )
        try:
            with instruments_path.open(newline="", encoding="utf-8") as handle:
                existing_instruments = list(csv.DictReader(handle))
            existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, csv.Error) as exc:
            raise ValueError(f"cannot read existing dataset metadata: {exc}") from exc
        if not isinstance(existing_manifest, dict):
            raise ValueError("existing manifest must contain a JSON object")

        existing_symbols = {
            str(item.get("symbol"))
            for item in existing_manifest.get("coverage", [])
            if isinstance(item, dict) and item.get("symbol")
        }
        existing_symbols.update(
            str(row.get("canonical_symbol"))
            for row in existing_instruments
            if row.get("canonical_symbol")
        )
        duplicates = sorted(existing_symbols.intersection(symbols))
        if duplicates:
            raise ValueError(
                f"symbols already exist in dataset: {duplicates}; remove them from the download list"
            )
    elif append:
        # --append is also safe for a newly-created, empty output directory.
        existing_manifest = {}

    start = datetime.now(UTC) - timedelta(days=min(days, 59))
    end = datetime.now(UTC)
    failures: list[dict[str, str]] = []
    coverage: list[dict[str, object]] = []
    instrument_rows: list[dict[str, str]] = []
    total_rows = 0
    existing_ids = {
        str(row.get("canonical_symbol")): int(row["instrument_id"])
        for row in existing_instruments
        if row.get("canonical_symbol") and row.get("instrument_id")
    }
    next_custom_id = max(
        (int(row["instrument_id"]) for row in existing_instruments if row.get("instrument_id")),
        default=0,
    ) + 1

    bars_mode = "a" if bars_path.exists() else "w"
    with bars_path.open(bars_mode, newline="", encoding="utf-8") as bars_handle:
        writer = csv.DictWriter(bars_handle, fieldnames=BAR_FIELDS)
        if bars_mode == "w":
            writer.writeheader()

        for symbol in symbols:
            instrument_id = existing_ids.get(symbol)
            if instrument_id is None:
                instrument_id = (
                    DEFAULT_SYMBOLS.index(symbol) + 1
                    if symbol in DEFAULT_SYMBOLS else next_custom_id
                )
                if symbol not in DEFAULT_SYMBOLS:
                    next_custom_id += 1

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
                    "symbol": symbol,
                    "rows": len(rows),
                    "first_bar": rows[0]["bar_start"],
                    "last_bar": rows[-1]["bar_start"],
                    "exchange_timezone": source_timezone,
                })
            except (FreeDataDownloadError, OSError, ValueError) as exc:
                failures.append({"symbol": symbol, "error": str(exc)})
            time.sleep(1.0)

    merged_instruments = existing_instruments + instrument_rows
    with instruments_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=(
            "instrument_id", "canonical_symbol", "profile", "market", "tradable", "source",
        ))
        writer.writeheader()
        writer.writerows(merged_instruments)

    previous_coverage = list(existing_manifest.get("coverage", []))
    previous_failures = list(existing_manifest.get("failed_symbols", []))
    attempted_symbols = set(symbols)
    merged_coverage = previous_coverage + coverage
    # Replace old failure records for symbols retried in this invocation.
    merged_failures = [
        item for item in previous_failures
        if not isinstance(item, dict) or item.get("symbol") not in attempted_symbols
    ] + failures
    previous_requested = existing_manifest.get("symbols_requested", [])
    manifest = {
        "provider": "Yahoo Finance public chart endpoint",
        "source_url": "https://query1.finance.yahoo.com/v8/finance/chart/",
        "downloaded_at_utc": datetime.now(UTC).isoformat(),
        "requested_days": days,
        "interval_seconds": INTERVAL_SECONDS,
        "source_interval": "5m",
        "adjustment_mode": "raw",
        "prepost": False,
        "symbols_requested": sorted(set(previous_requested) | set(symbols)),
        "symbols_downloaded": [item["symbol"] for item in merged_coverage],
        "coverage": merged_coverage,
        "total_rows": int(existing_manifest.get("total_rows", 0)) + total_rows,
        "failed_symbols": merged_failures,
        "limitations": [
            "Intraday history is limited to the provider's recent retention window (maximum 60 calendar days).",
            "OHLCV does not provide consolidated volume, bid/ask quotes, or trade counts; trade_count=0 is a schema placeholder.",
            "Missing source volume is recorded as zero and flagged; it is not inferred.",
            "Data is unadjusted raw OHLCV; review corporate actions before long-horizon use.",
            "Review Yahoo Finance terms; yfinance documents this access as intended for personal use.",
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    with (output / "download_failures.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("symbol", "error"))
        writer.writeheader()
        writer.writerows(merged_failures)

    return {
        "output_dir": str(output),
        "symbols_requested": len(symbols),
        "symbols_downloaded": len(coverage),
        "symbols_failed": len(failures),
        "rows": total_rows,
        "interval_seconds": INTERVAL_SECONDS,
        "days_requested": days,
        "failures": failures,
    }
