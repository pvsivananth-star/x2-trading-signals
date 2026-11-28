"""Preload market bars from a data folder before any simulation starts.

Accepted inputs (one folder per run, one interval per run):
  A) one combined CSV with header  symbol,timestamp,open,high,low,close,volume   (preferred)
  B) one CSV per symbol (file stem = symbol) with header  timestamp,open,high,low,close,volume

timestamp is the UTC bar START, e.g. 2026-11-02T14:30:00Z. Spacing is inferred from the data and
must equal --interval exactly; a mismatch stops the run. Every symbol must appear in exactly one
market profile in config/markets_17.yaml. Validation fails closed with the file and row.
"""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any

import yaml

from app.backtest.bars import Bar

PRICE_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")
LONG_COLUMNS = ("symbol",) + PRICE_COLUMNS


class PreloadError(ValueError):
    """Raised when a data folder cannot be loaded exactly."""


@dataclass(frozen=True, slots=True)
class PreloadedDataset:
    directory: Path
    interval_seconds: int
    grouped: dict[tuple[int, int], list[Bar]]
    instrument_symbols: dict[int, str]
    profiles: dict[str, str]
    tick_index: list[tuple[datetime, str]]
    summary: dict[str, Any] = field(default_factory=dict)


def _parse_utc(text: str) -> datetime:
    value = text.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PreloadError(f"unparseable timestamp: {text}") from exc
    if parsed.tzinfo is None:
        raise PreloadError(f"timestamp without UTC designator: {text}")
    return parsed


def resolve_profile(symbol: str, markets: dict[str, Any], *, allow_backtest_excluded: bool = True) -> str | None:
    """Resolve a symbol to one market profile.

    Backtest fixtures may intentionally use production-excluded instruments such as index/futures
    symbols. Those remain blocked by live policy, but the dataset loader must still know their
    market profile so the backtest can exercise the category.
    """
    matches = [
        name for name, cfg in markets["markets"].items()
        if symbol in cfg.get("core_symbols", [])
        or (allow_backtest_excluded and symbol in cfg.get("excluded_symbols", []))
    ]
    if len(matches) > 1:
        raise PreloadError(f"symbol {symbol} is listed in more than one profile: {sorted(matches)}")
    return matches[0] if matches else None


def _validated_row(name: str, line_no: int, row: dict[str, str]) -> tuple[datetime, Decimal, Decimal, Decimal, Decimal, Decimal]:
    try:
        start = _parse_utc(row["timestamp"])
        o, h, l, c = (Decimal(row[k]) for k in ("open", "high", "low", "close"))
        volume = Decimal(row["volume"])
    except (KeyError, ValueError, ArithmeticError) as exc:
        raise PreloadError(f"{name} row {line_no}: unparseable value ({exc})") from exc
    if min(o, h, l, c) <= 0:
        raise PreloadError(f"{name} row {line_no}: non-positive price")
    if h < max(o, c) or l > min(o, c) or h < l:
        raise PreloadError(f"{name} row {line_no}: OHLC bounds violated")
    if volume < 0:
        raise PreloadError(f"{name} row {line_no}: negative volume")
    return start, o, h, l, c, volume


def _read_inputs(directory: Path) -> dict[str, list[tuple[datetime, Decimal, Decimal, Decimal, Decimal, Decimal]]]:
    """Return {symbol: [bar tuples]} from either a combined file or per-symbol files."""
    csv_files = sorted(p for p in directory.glob("*.csv") if p.is_file())
    if not csv_files:
        raise PreloadError(f"no CSV files in {directory}")
    combined = []
    for path in csv_files:
        with path.open(newline="", encoding="utf-8") as handle:
            header = next(csv.reader(handle), [])
        if "symbol" in header:
            combined.append(path)
    if combined and len(csv_files) != 1:
        raise PreloadError(f"combined input must be the only CSV in {directory}; found {len(csv_files)} files")

    series: dict[str, list[tuple[datetime, Decimal, Decimal, Decimal, Decimal, Decimal]]] = {}
    if combined:
        path = combined[0]
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            missing = set(LONG_COLUMNS) - set(reader.fieldnames or [])
            if missing:
                raise PreloadError(f"{path.name}: missing columns {sorted(missing)}")
            for line_no, row in enumerate(reader, start=2):
                symbol = (row["symbol"] or "").strip()
                if not symbol:
                    raise PreloadError(f"{path.name} row {line_no}: empty symbol")
                series.setdefault(symbol, []).append(_validated_row(path.name, line_no, row))
    else:
        for path in csv_files:
            symbol = path.stem
            with path.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                missing = set(PRICE_COLUMNS) - set(reader.fieldnames or [])
                if missing:
                    raise PreloadError(f"{path.name}: missing columns {sorted(missing)}")
                series[symbol] = [_validated_row(path.name, line_no, row)
                                  for line_no, row in enumerate(reader, start=2)]
    return series


def preload_directory(directory: str | Path, interval_seconds: int,
                      markets_path: str | Path = "config/markets_17.yaml") -> PreloadedDataset:
    directory = Path(directory)
    if not directory.is_dir():
        raise PreloadError(f"data directory not found: {directory}")
    if interval_seconds <= 0:
        raise PreloadError("interval must be positive")
    markets = yaml.safe_load(Path(markets_path).read_text(encoding="utf-8"))
    series = _read_inputs(directory)
    if not series:
        raise PreloadError(f"no bars found in {directory}")

    # Per-symbol checks: strictly increasing, no duplicates, known profile.
    deltas: Counter[int] = Counter()
    for symbol, rows in series.items():
        if not rows:
            raise PreloadError(f"{symbol}: no bars")
        profile = resolve_profile(symbol, markets, allow_backtest_excluded=True)
        if profile is None:
            raise PreloadError(f"{symbol}: no market profile lists this symbol in markets_17.yaml")
        for previous, current in pairwise(rows):
            step = current[0] - previous[0]
            if step.total_seconds() == 0:
                raise PreloadError(f"{symbol}: duplicate bar at {current[0].isoformat()}")
            if step.total_seconds() < 0:
                raise PreloadError(f"{symbol}: timestamps out of order at {current[0].isoformat()}")
            deltas[int(step.total_seconds())] += 1

    # Spacing: the dominant step must equal --interval, and every other step must be a
    # whole multiple of it (a multiple means a gap in the data, not a different interval).
    if deltas:
        inferred, _count = deltas.most_common(1)[0]
        if inferred != interval_seconds:
            raise PreloadError(
                f"data bar spacing is {inferred}s but --interval is {interval_seconds}s; "
                f"use --interval {inferred} or a file with {interval_seconds}s bars"
            )
        for step in deltas:
            if step % interval_seconds != 0:
                raise PreloadError(f"bar spacing {step}s is not a multiple of --interval {interval_seconds}s")
    else:
        inferred = interval_seconds  # one bar per symbol: spacing cannot be checked

    grouped: dict[tuple[int, int], list[Bar]] = {}
    instrument_symbols: dict[int, str] = {}
    profiles: dict[str, str] = {}
    tick_index: list[tuple[datetime, str]] = []
    per_symbol: dict[str, Any] = {}
    for idx, symbol in enumerate(sorted(series), start=1):
        rows = series[symbol]
        bars = [Bar(start, start + timedelta(seconds=interval_seconds), o, h, l, c, v, 0)
                for start, o, h, l, c, v in rows]
        gaps = sum(1 for a, b in pairwise(bars) if b.bar_start - a.bar_start > timedelta(seconds=interval_seconds))
        profile = resolve_profile(symbol, markets)
        instrument_symbols[idx] = symbol
        profiles[symbol] = profile
        grouped[(idx, interval_seconds)] = bars
        tick_index.extend((bar.bar_end, symbol) for bar in bars)
        per_symbol[symbol] = {"profile": profile, "bars": len(bars), "gaps": gaps,
                              "first": bars[0].bar_start.isoformat(), "last": bars[-1].bar_start.isoformat()}
    tick_index.sort()
    summary = {
        "format": "combined-csv" if len(series) > 0 and _is_combined(directory) else "per-symbol-csv",
        "interval_seconds": interval_seconds,
        "inferred_spacing_seconds": inferred,
        "symbols_loaded": len(series),
        "total_bars": len(tick_index),
        "first_tick": tick_index[0][0].isoformat(),
        "last_tick": tick_index[-1][0].isoformat(),
        "symbols": per_symbol,
    }
    return PreloadedDataset(directory, interval_seconds, grouped, instrument_symbols, profiles,
                            tick_index, summary)


def _is_combined(directory: Path) -> bool:
    for path in directory.glob("*.csv"):
        with path.open(newline="", encoding="utf-8") as handle:
            if "symbol" in (next(csv.reader(handle), []) or []):
                return True
    return False
