"""Input contract for the backtest loader: combined file, per-symbol files, spacing, gaps, errors."""

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.backtest.preload import PreloadError, preload_directory

MARKETS = Path(__file__).resolve().parents[2] / "config" / "markets_17.yaml"
LONG_HEADER = "symbol,timestamp,open,high,low,close,volume\n"
PLAIN_HEADER = "timestamp,open,high,low,close,volume\n"


def _ts(minutes: int, start=datetime(2026, 11, 2, 14, 30, tzinfo=UTC)) -> str:
    return (start + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _line(symbol: str | None, minutes: int, price: float = 100.0) -> str:
    row = f"{_ts(minutes)},{price:.4f},{price + 0.05:.4f},{price - 0.05:.4f},{price + 0.02:.4f},1000"
    return (f"{symbol}," if symbol is not None else "") + row + "\n"


def _dir(files: dict[str, str]) -> Path:
    tmp = Path(tempfile.mkdtemp())
    for name, body in files.items():
        (tmp / name).write_text(body, encoding="utf-8")
    return tmp


class CombinedFileTests(unittest.TestCase):
    def test_combined_file_loads_every_symbol(self):
        body = LONG_HEADER + "".join(_line("SPY", m) for m in (0, 5, 10)) + "".join(_line("GLD", m) for m in (0, 5, 10))
        ds = preload_directory(_dir({"market_bars_5m.csv": body}), 300, MARKETS)
        self.assertEqual(ds.summary["format"], "combined-csv")
        self.assertEqual(ds.summary["total_bars"], 6)
        self.assertEqual(ds.profiles["SPY"], "US_EQUITIES")
        self.assertEqual(ds.profiles["GLD"], "US_COMMODITIES")
        self.assertEqual(ds.summary["inferred_spacing_seconds"], 300)

    def test_combined_file_must_be_the_only_csv(self):
        body = LONG_HEADER + _line("SPY", 0)
        with self.assertRaisesRegex(PreloadError, "must be the only CSV"):
            preload_directory(_dir({"a.csv": body, "b.csv": PLAIN_HEADER + _line(None, 0)}), 300, MARKETS)


class SpacingTests(unittest.TestCase):
    def test_one_minute_data_with_five_minute_interval_is_rejected(self):
        body = LONG_HEADER + "".join(_line("SPY", m) for m in range(30))
        with self.assertRaisesRegex(PreloadError, "bar spacing is 60s but --interval is 300s"):
            preload_directory(_dir({"market_bars_1m.csv": body}), 300, MARKETS)

    def test_per_symbol_one_minute_files_with_five_minute_interval_are_rejected(self):
        body = PLAIN_HEADER + "".join(_line(None, m) for m in range(30))
        with self.assertRaisesRegex(PreloadError, "bar spacing is 60s"):
            preload_directory(_dir({"SPY.csv": body}), 300, MARKETS)

    def test_missing_bar_is_a_gap_not_an_error(self):
        body = LONG_HEADER + "".join(_line("SPY", m) for m in (0, 5, 15, 20))
        ds = preload_directory(_dir({"market_bars_5m.csv": body}), 300, MARKETS)
        self.assertEqual(ds.summary["symbols"]["SPY"]["gaps"], 1)

    def test_duplicate_bar_is_rejected(self):
        body = LONG_HEADER + _line("SPY", 0) + _line("SPY", 5) + _line("SPY", 5)
        with self.assertRaisesRegex(PreloadError, "duplicate bar"):
            preload_directory(_dir({"market_bars_5m.csv": body}), 300, MARKETS)


class FieldErrorTests(unittest.TestCase):
    def test_empty_symbol_rejected(self):
        body = LONG_HEADER + _line("", 0)
        with self.assertRaisesRegex(PreloadError, "empty symbol"):
            preload_directory(_dir({"market_bars_5m.csv": body}), 300, MARKETS)

    def test_unknown_symbol_rejected(self):
        body = LONG_HEADER + _line("ZZZZ", 0) + _line("ZZZZ", 5)
        with self.assertRaisesRegex(PreloadError, "no market profile"):
            preload_directory(_dir({"market_bars_5m.csv": body}), 300, MARKETS)

    def test_missing_column_rejected(self):
        body = "symbol,timestamp,open,high,low,close\nSPY,2026-11-02T14:30:00Z,1,2,0.5,1.5\n"
        with self.assertRaisesRegex(PreloadError, "missing columns"):
            preload_directory(_dir({"market_bars_5m.csv": body}), 300, MARKETS)

    def test_timestamp_without_utc_rejected(self):
        body = LONG_HEADER + "SPY,2026-11-02T14:30:00,100,100.05,99.95,100.02,1000\n"
        with self.assertRaisesRegex(PreloadError, "UTC designator"):
            preload_directory(_dir({"market_bars_5m.csv": body}), 300, MARKETS)

    def test_missing_folder_rejected(self):
        with self.assertRaisesRegex(PreloadError, "data directory not found"):
            preload_directory("/no/such/folder", 300, MARKETS)


if __name__ == "__main__":
    unittest.main()
