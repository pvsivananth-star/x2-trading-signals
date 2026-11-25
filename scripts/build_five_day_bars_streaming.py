from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import sys

ROOT_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_REPO))

from app.providers.binance_public import BinancePublicTradeAdapter

UTC = timezone.utc
ROOT = Path("data/fixtures/binance/BTCUSDT/5-day-2026-11-20-to-2026-11-24")
RAW = ROOT / "raw"
OUT = ROOT / "bars"


def main() -> None:
    intervals = (60, 300, 900)
    buckets: dict[int, dict[datetime, dict[str, object]]] = {interval: {} for interval in intervals}
    adapter = BinancePublicTradeAdapter("BTCUSDT")

    parsed = 0
    for path in sorted(RAW.glob("*.zip")):
        for row in adapter.iter_dump_rows(path):
            try:
                event = adapter.parse_dump_row(row, datetime.now(UTC))
            except (TypeError, ValueError, OverflowError):
                continue
            parsed += 1
            for interval in intervals:
                bucket = _bucket(event.event_time, interval)
                state = buckets[interval].setdefault(bucket, {
                    "open": event.price,
                    "high": event.price,
                    "low": event.price,
                    "close": event.price,
                    "volume": Decimal("0"),
                    "count": 0,
                })
                state["high"] = max(state["high"], event.price)
                state["low"] = min(state["low"], event.price)
                state["close"] = event.price
                state["volume"] = state["volume"] + event.quantity
                state["count"] = state["count"] + 1

    OUT.mkdir(parents=True, exist_ok=True)
    for interval in intervals:
        path = OUT / f"BTCUSDT-{interval//60}m.csv"
        with path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.writer(output)
            writer.writerow(["bar_start", "bar_end", "open", "high", "low", "close", "volume", "trade_count"])
            for start in sorted(buckets[interval]):
                state = buckets[interval][start]
                writer.writerow([
                    start.isoformat(), (start + timedelta(seconds=interval)).isoformat(),
                    state["open"], state["high"], state["low"], state["close"],
                    state["volume"], state["count"],
                ])
        print(f"wrote {path} ({len(buckets[interval])} bars)")
    print(f"parsed {parsed} trade rows")


def _bucket(timestamp: datetime, interval_seconds: int) -> datetime:
    timestamp = timestamp.astimezone(UTC)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    elapsed = timestamp - epoch
    bucket_seconds = (elapsed.days * 86400 + elapsed.seconds) // interval_seconds * interval_seconds
    return epoch + timedelta(seconds=bucket_seconds)


if __name__ == "__main__":
    main()
