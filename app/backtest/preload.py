"""Preload per-symbol bar files and build one exact, chronological tick index before any simulation.

Accepted layout: a folder with one CSV per symbol (file stem = symbol), each with the header
    timestamp,open,high,low,close,volume
where timestamp is the UTC bar START (e.g. 2026-11-02T14:30:00Z). Every file is fully read,
validated, and resolved to a market profile from config/markets_17.yaml before any strategy runs.

Validation fails closed: unknown symbols, non-increasing timestamps, duplicate bars, non-positive
prices, or OHLC bounds violations stop the run with a message naming the file and row.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from app.backtest.bars import Bar

REQUIRED_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")


class PreloadError(ValueError):
    """Raised when a folder cannot be preloaded exactly."""


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
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise PreloadError(f"timestamp without UTC designator: {text}")
    return parsed


def resolve_profile(symbol: str, markets: dict[str, Any]) -> str | None:
    """Return the market profile whose core_symbols list contains the symbol, or None."""
    matches = [name for name, cfg in markets["markets"].items() if symbol in cfg.get("core_symbols", [])]
    if len(matches) > 1:
        raise PreloadError(f"symbol {symbol} is listed in more than one profile: {sorted(matches)}")
    return matches[0] if matches else None


def _load_symbol_file(path: Path, interval_seconds: int) -> tuple[list[Bar], int]:
    bars: list[Bar] = []
    gaps = 0
    previous_start: datetime | None = None
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = set(REQUIRED_COLUMNS) - set(reader.fieldnames or [])
        if missing:
            raise PreloadError(f"{path.name}: missing columns {sorted(missing)}")
        for line_no, row in enumerate(reader, start=2):
            try:
                start = _parse_utc(row["timestamp"])
                o, h, l, c = (Decimal(row[k]) for k in ("open", "high", "low", "close"))
                volume = Decimal(row["volume"])
            except (ValueError, ArithmeticError, KeyError) as exc:
                raise PreloadError(f"{path.name} row {line_no}: unparseable value ({exc})") from exc
            if min(o, h, l, c) <= 0:
                raise PreloadError(f"{path.name} row {line_no}: non-positive price")
            if h < max(o, c) or l > min(o, c) or h < l:
                raise PreloadError(f"{path.name} row {line_no}: OHLC bounds violated")
            if volume < 0:
                raise PreloadError(f"{path.name} row {line_no}: negative volume")
            if previous_start is not None:
                if start == previous_start:
                    raise PreloadError(f"{path.name} row {line_no}: duplicate bar at {start.isoformat()}")
                if start < previous_start:
                    raise PreloadError(f"{path.name} row {line_no}: timestamps out of order")
                if start - previous_start != timedelta(seconds=interval_seconds):
                    gaps += 1
            previous_start = start
            end = start + timedelta(seconds=interval_seconds)
            bars.append(Bar(start, end, o, h, l, c, volume, 0))
    if not bars:
        raise PreloadError(f"{path.name}: no bars")
    return bars, gaps


def preload_directory(directory: str | Path, interval_seconds: int,
                      markets_path: str | Path = "config/markets_17.yaml") -> PreloadedDataset:
    directory = Path(directory)
    if not directory.is_dir():
        raise PreloadError(f"data directory not found: {directory}")
    markets = yaml.safe_load(Path(markets_path).read_text(encoding="utf-8"))
    files = sorted(p for p in directory.glob("*.csv") if p.is_file())
    if not files:
        raise PreloadError(f"no per-symbol CSV files in {directory}")

    grouped: dict[tuple[int, int], list[Bar]] = {}
    instrument_symbols: dict[int, str] = {}
    profiles: dict[str, str] = {}
    tick_index: list[tuple[datetime, str]] = []
    per_symbol: dict[str, Any] = {}
    for idx, path in enumerate(files, start=1):
        symbol = path.stem
        profile = resolve_profile(symbol, markets)
        if profile is None:
            raise PreloadError(f"{symbol}: no market profile lists this symbol in markets_17.yaml")
        bars, gaps = _load_symbol_file(path, interval_seconds)
        instrument_symbols[idx] = symbol
        profiles[symbol] = profile
        grouped[(idx, interval_seconds)] = bars
        tick_index.extend((bar.bar_end, symbol) for bar in bars)
        per_symbol[symbol] = {"profile": profile, "bars": len(bars), "gaps": gaps,
                              "first": bars[0].bar_start.isoformat(), "last": bars[-1].bar_start.isoformat()}
    tick_index.sort()
    summary = {
        "format": "per-symbol-csv",
        "files": len(files),
        "total_bars": len(tick_index),
        "first_tick": tick_index[0][0].isoformat(),
        "last_tick": tick_index[-1][0].isoformat(),
        "symbols": per_symbol,
    }
    return PreloadedDataset(directory, interval_seconds, grouped, instrument_symbols, profiles,
                            tick_index, summary)
