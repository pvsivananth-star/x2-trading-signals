from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from app.backtest.validation import validate_dataset


class Phase9DatasetValidationTests(unittest.TestCase):
    def _dataset(self, *, rows: list[dict[str, str]], manifest: dict | None = None) -> Path:
        root = Path(tempfile.mkdtemp())
        (root / "instruments.csv").write_text(
            "instrument_id,canonical_symbol\n1,SIM-EQ\n", encoding="utf-8"
        )
        fields = [
            "instrument_id", "interval_seconds", "bar_start", "bar_end",
            "open_price", "high_price", "low_price", "close_price",
            "volume", "trade_count", "is_complete", "quality_status",
        ]
        with (root / "bars.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        if manifest is not None:
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return root

    @staticmethod
    def _row(start: str = "2026-11-20T14:30:00+00:00", **overrides: str) -> dict[str, str]:
        row = {
            "instrument_id": "1",
            "interval_seconds": "300",
            "bar_start": start,
            "bar_end": "2026-11-20T14:35:00+00:00",
            "open_price": "100",
            "high_price": "101",
            "low_price": "99",
            "close_price": "100.5",
            "volume": "1000",
            "trade_count": "10",
            "is_complete": "true",
            "quality_status": "ok",
        }
        row.update(overrides)
        return row

    def test_valid_dataset(self) -> None:
        root = self._dataset(
            rows=[self._row()],
            manifest={"fixture": "synthetic", "source": "deterministic_synthetic_test_fixture",
                      "redistribution_allowed": True},
        )
        report = validate_dataset(root, interval_seconds=300)
        self.assertTrue(report.valid)
        self.assertTrue(report.synthetic)

    def test_duplicate_bar_is_critical(self) -> None:
        row = self._row()
        report = validate_dataset(self._dataset(rows=[row, row]), interval_seconds=300)
        self.assertFalse(report.valid)
        self.assertIn("DUPLICATE_BAR", {issue.code for issue in report.critical})

    def test_out_of_order_source_is_critical(self) -> None:
        first = self._row("2026-11-20T14:35:00+00:00",
                          bar_end="2026-11-20T14:40:00+00:00")
        second = self._row("2026-11-20T14:30:00+00:00")
        report = validate_dataset(self._dataset(rows=[first, second]), interval_seconds=300)
        self.assertFalse(report.valid)
        self.assertIn("OUT_OF_ORDER", {issue.code for issue in report.critical})

    def test_gap_is_warning_by_default(self) -> None:
        first = self._row()
        second = self._row("2026-11-20T14:45:00+00:00",
                           bar_end="2026-11-20T14:50:00+00:00")
        report = validate_dataset(self._dataset(rows=[first, second]), interval_seconds=300)
        self.assertTrue(report.valid)
        self.assertIn("DATA_GAP", {issue.code for issue in report.warnings})

    def test_gap_can_be_promoted_to_failure(self) -> None:
        first = self._row()
        second = self._row("2026-11-20T14:45:00+00:00",
                           bar_end="2026-11-20T14:50:00+00:00")
        report = validate_dataset(
            self._dataset(rows=[first, second]),
            interval_seconds=300,
            fail_on_warnings=True,
        )
        self.assertFalse(report.valid)

    def test_incomplete_bar_is_critical(self) -> None:
        report = validate_dataset(
            self._dataset(rows=[self._row(is_complete="false")]),
            interval_seconds=300,
        )
        self.assertFalse(report.valid)
        self.assertIn("INCOMPLETE_BAR", {issue.code for issue in report.critical})

    def test_interval_mismatch_is_critical(self) -> None:
        report = validate_dataset(
            self._dataset(rows=[self._row(interval_seconds="60")]),
            interval_seconds=300,
        )
        self.assertFalse(report.valid)
        self.assertIn("INTERVAL_MISMATCH", {issue.code for issue in report.critical})
