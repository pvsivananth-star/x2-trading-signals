"""Loader for locally held market-bar CSV files (for example the NASDAQ/Coinbase bar exports).

Policy: licensed market data must NEVER be committed to this repository. The loader reads from
the directory named by the X2_REAL_DATA_DIR environment variable and refuses any directory
inside the repository checkout. See docs/data-policy.md.
"""

from __future__ import annotations

import csv
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from app.backtest.bars import Bar

REAL_DATA_ENV = "X2_REAL_DATA_DIR"
REPO_ROOT = Path(__file__).resolve().parents[1]
BARS_FILE = "bars_1.csv"

REQUIRED_COLUMNS = (
    "instrument_id", "interval_seconds", "bar_start", "bar_end", "open_price", "high_price",
    "low_price", "close_price", "volume", "trade_count", "is_complete", "quality_status",
)


class RealDataError(ValueError):
    """Raised when a bar file is malformed or the data directory is not allowed."""


def real_data_dir() -> Path | None:
    """Return the configured data directory, or None when the environment variable is unset."""
    raw = os.environ.get(REAL_DATA_ENV)
    if not raw:
        return None
    path = Path(raw).expanduser().resolve()
    if path == REPO_ROOT or REPO_ROOT in path.parents:
        raise RealDataError(
            f"{REAL_DATA_ENV} points inside the repository; licensed data must live outside it"
        )
    return path


def _parse_ts(text: str) -> datetime:
    value = text.strip().replace("Z", "+00:00")
    if value.endswith(("+00", "-00")):
        value += ":00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise RealDataError(f"timestamp without timezone: {text}")
    return parsed


@dataclass(frozen=True, slots=True)
class BarRecord:
    instrument_id: int
    interval_seconds: int
    bar: Bar


def load_bar_records(path: str | Path) -> list[BarRecord]:
    """Parse and validate a bars CSV. Raises RealDataError with the file row number on failure."""
    path = Path(path)
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = set(REQUIRED_COLUMNS) - set(reader.fieldnames or [])
        if missing:
            raise RealDataError(f"{path.name}: missing columns {sorted(missing)}")
        records: list[BarRecord] = []
        for line_no, row in enumerate(reader, start=2):
            try:
                interval = int(row["interval_seconds"])
                start = _parse_ts(row["bar_start"])
                end = _parse_ts(row["bar_end"])
                open_, high, low, close = (Decimal(row[k]) for k in ("open_price", "high_price", "low_price", "close_price"))
                volume = Decimal(row["volume"])
                trades = int(row["trade_count"])
            except (ValueError, ArithmeticError, KeyError) as exc:
                raise RealDataError(f"{path.name} row {line_no}: unparseable value ({exc})") from exc
            if interval <= 0:
                raise RealDataError(f"{path.name} row {line_no}: interval must be positive")
            if end - start != timedelta(seconds=interval):
                raise RealDataError(f"{path.name} row {line_no}: bar_end is not bar_start + interval")
            if min(open_, high, low, close) <= 0:
                raise RealDataError(f"{path.name} row {line_no}: non-positive price")
            if high < max(open_, close) or low > min(open_, close) or high < low:
                raise RealDataError(f"{path.name} row {line_no}: OHLC bounds violated")
            if volume < 0 or trades < 0:
                raise RealDataError(f"{path.name} row {line_no}: negative volume or trade count")
            bar = Bar(start, end, open_, high, low, close, volume, trades)
            records.append(BarRecord(int(row["instrument_id"]), interval, bar))
    return records


def group_bars(records: list[BarRecord]) -> dict[tuple[int, int], list[Bar]]:
    """Group bars by (instrument_id, interval_seconds), sorted by bar end time."""
    grouped: dict[tuple[int, int], list[Bar]] = defaultdict(list)
    for record in records:
        grouped[(record.instrument_id, record.interval_seconds)].append(record.bar)
    for bars in grouped.values():
        bars.sort(key=lambda b: b.bar_end)
    return dict(grouped)


def load_real_bars() -> dict[tuple[int, int], list[Bar]] | None:
    """Load the configured bar file, or return None when no data directory is configured."""
    directory = real_data_dir()
    if directory is None:
        return None
    return group_bars(load_bar_records(directory / BARS_FILE))
