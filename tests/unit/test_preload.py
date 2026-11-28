"""Preload: every per-symbol file is validated and merged into one chronological tick index."""

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from app.backtest.dataset_runner import run_dataset
from app.backtest.preload import PreloadError, preload_directory, resolve_profile

MARKETS = Path(__file__).resolve().parents[2] / "config" / "markets_17.yaml"
HEADER = "timestamp,open,high,low,close,volume\n"


def _rows(n, start=datetime(2026, 11, 2, 14, 30, tzinfo=UTC), step=5, price=100.0):
    lines = []
    for i in range(n):
        ts = (start + timedelta(minutes=step * i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        p = price + i * 0.01
        lines.append(f"{ts},{p:.4f},{p + 0.05:.4f},{p - 0.05:.4f},{p + 0.02:.4f},{1000 + i}\n")
    return "".join(lines)


def _folder(files: dict[str, str]) -> Path:
    tmp = Path(tempfile.mkdtemp())
    for name, body in files.items():
        (tmp / name).write_text(HEADER + body, encoding="utf-8")
    return tmp


class PreloadTests(unittest.TestCase):
    def test_profiles_resolve_from_config(self):
        markets = yaml.safe_load(MARKETS.read_text())
        self.assertEqual(resolve_profile("SPY", markets), "US_EQUITIES")
        self.assertEqual(resolve_profile("BTC-USD", markets), "CRYPTO_MAJORS")
        self.assertIsNone(resolve_profile("NOT-A-SYMBOL", markets))

    def test_merged_tick_index_is_chronological(self):
        folder = _folder({"SPY.csv": _rows(4), "BTC-USD.csv": _rows(4, start=datetime(2026, 11, 2, 14, 32, tzinfo=UTC))})
        ds = preload_directory(folder, 300, MARKETS)
        self.assertEqual(ds.summary["total_bars"], 8)
        ticks = [t for t, _ in ds.tick_index]
        self.assertEqual(ticks, sorted(ticks))
        self.assertEqual(ds.profiles["SPY"], "US_EQUITIES")

    def test_unknown_symbol_fails_closed(self):
        folder = _folder({"ZZZZ.csv": _rows(3)})
        with self.assertRaisesRegex(PreloadError, "no market profile"):
            preload_directory(folder, 300, MARKETS)

    def test_duplicate_timestamp_rejected(self):
        lines = _rows(2).splitlines(keepends=True)
        body = "".join(lines + [lines[-1]])
        with self.assertRaisesRegex(PreloadError, "duplicate bar"):
            preload_directory(_folder({"SPY.csv": body}), 300, MARKETS)

    def test_out_of_order_rejected(self):
        lines = _rows(3).splitlines(keepends=True)
        with self.assertRaisesRegex(PreloadError, "out of order"):
            preload_directory(_folder({"SPY.csv": lines[2] + lines[1]}), 300, MARKETS)

    def test_bad_ohlc_rejected_with_row(self):
        bad = "2026-11-02T14:30:00Z,100,99,98,100,1000\n"
        with self.assertRaisesRegex(PreloadError, "row 2: OHLC"):
            preload_directory(_folder({"SPY.csv": bad}), 300, MARKETS)

    def test_run_dataset_on_folder_excludes_outside_baseline_with_reason(self):
        folder = _folder({"SPY.csv": _rows(30), "BTC-USD.csv": _rows(30)})
        result = run_dataset(folder, interval_seconds=300, markets_path=MARKETS)
        self.assertEqual(result["preload"]["format"], "per-symbol-csv")
        self.assertIn("SPY", result["per_symbol"])
        self.assertNotIn("BTC-USD", result["per_symbol"])
        reasons = {e["symbol"]: e["reason"] for e in result["excluded_instruments"]}
        self.assertEqual(reasons.get("BTC-USD"), "outside_baseline_scope")

    def test_default_run_on_bars_csv_folder_excludes_instead_of_raising(self):
        fixture = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "synthetic_ci"
        result = run_dataset(fixture, interval_seconds=300, markets_path=MARKETS)
        reasons = {e["symbol"]: e["reason"] for e in result["excluded_instruments"]}
        self.assertEqual(reasons.get("SIM-COIN"), "outside_baseline_scope")


if __name__ == "__main__":
    unittest.main()
