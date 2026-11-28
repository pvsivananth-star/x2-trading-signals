from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.backtest.free_data import download_free_dataset
from app.backtest.validation import validate_dataset


class FreeHistoricalDataTests(unittest.TestCase):
    @staticmethod
    def _rows() -> list[dict[str, str]]:
        return [{
            "bar_start": "2026-11-20T14:30:00+00:00",
            "bar_end": "2026-11-20T14:35:00+00:00",
            "open_price": "100",
            "high_price": "101",
            "low_price": "99",
            "close_price": "100.5",
            "volume": "1000",
            "trade_count": "0",
            "quality_status": "ok",
        }]

    def test_download_writes_backtest_compatible_real_data(self) -> None:
        output = Path(tempfile.mkdtemp()) / "dataset"
        with patch(
            "app.backtest.free_data._fetch_symbol",
            return_value=(self._rows(), "America/New_York"),
        ):
            result = download_free_dataset(
                output, symbols=("SPY",), days=30,
                profile_map={"SPY": "US_EQUITIES"},
            )
        self.assertEqual(result["symbols_downloaded"], 1)
        report = validate_dataset(output, interval_seconds=300)
        self.assertTrue(report.valid)
        self.assertFalse(report.synthetic)
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["provider"], "Yahoo Finance public chart endpoint")
        self.assertEqual(manifest["total_rows"], 1)

    def test_download_refuses_to_overwrite_existing_dataset(self) -> None:
        output = Path(tempfile.mkdtemp())
        (output / "bars.csv").write_text("existing", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            download_free_dataset(
                output, symbols=("SPY",), profile_map={"SPY": "US_EQUITIES"},
            )

    def test_provider_retention_limit_is_enforced(self) -> None:
        with self.assertRaisesRegex(ValueError, "limited to 60"):
            download_free_dataset(
                Path(tempfile.mkdtemp()) / "dataset",
                symbols=("SPY",), days=61, profile_map={"SPY": "US_EQUITIES"},
            )

    def test_unprofiled_symbols_are_rejected_before_network_access(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing backtest profiles"):
            download_free_dataset(
                Path(tempfile.mkdtemp()) / "dataset",
                symbols=("UNKNOWN",), profile_map={},
            )


if __name__ == "__main__":
    unittest.main()
