from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

import csv
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from app.backtest.bars import Bar
from app.backtest.causality import validate_causal_window, validate_next_bar_execution
from app.backtest.dataset_runner import (
    InsufficientScoredDataError,
    dump_synthetic_warmup,
    inspect_scored_data,
    run_dataset,
)
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

    def test_unprofiled_symbol_can_be_blocked(self) -> None:
        report = validate_dataset(
            self._dataset(rows=[self._row()]),
            interval_seconds=300,
            profile_map={"OTHER": "US_EQUITIES"},
            require_profile=True,
        )
        self.assertFalse(report.valid)
        self.assertIn("UNPROFILED_SYMBOL", {issue.code for issue in report.critical})

    def test_disabled_instrument_is_blocked(self) -> None:
        root = Path(tempfile.mkdtemp())
        (root / "instruments.csv").write_text(
            "instrument_id,canonical_symbol,tradable\n1,SIM-EQ,false\n", encoding="utf-8"
        )
        row = self._row()
        with (root / "bars.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=row.keys())
            writer.writeheader()
            writer.writerow(row)
        report = validate_dataset(root, interval_seconds=300)
        self.assertFalse(report.valid)
        self.assertIn("INSTRUMENT_NOT_ELIGIBLE", {issue.code for issue in report.critical})

    def test_warmup_must_precede_scored_bars(self) -> None:
        first = self._row(is_warmup="false")
        second = self._row(
            "2026-11-20T14:35:00+00:00",
            bar_end="2026-11-20T14:40:00+00:00",
            is_warmup="true",
        )
        report = validate_dataset(self._dataset(rows=[first, second]), interval_seconds=300)
        self.assertFalse(report.valid)
        self.assertIn("WARMUP_AFTER_SCORED", {issue.code for issue in report.critical})

    def test_synthetic_warmup_dump_creates_self_contained_dataset(self) -> None:
        root = self._dataset(
            rows=[
                self._row("2026-11-20T14:30:00+00:00"),
                self._row("2026-11-21T14:30:00+00:00", bar_end="2026-11-21T14:35:00+00:00"),
            ]
        )
        output = Path(tempfile.mkdtemp()) / "warmup"
        result = dump_synthetic_warmup(
            root, output, interval_seconds=300, warmup_sessions=1,
            profile_map={"SIM-EQ": "US_EQUITIES"},
        )
        self.assertEqual(result["warmup_rows"], 1)
        self.assertTrue((output / "bars.csv").is_file())
        self.assertTrue((output / "instruments.csv").is_file())
        self.assertTrue((output / "manifest.json").is_file())
        with (output / "bars.csv").open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["is_warmup"], "true")
        report = validate_dataset(output, interval_seconds=300)
        self.assertTrue(report.valid)
        self.assertTrue(report.synthetic)
        self.assertEqual(report.warmup_rows, 1)


    def test_insufficient_scored_sessions_is_rejected_per_symbol(self) -> None:
        root = self._dataset(rows=[
            self._row("2026-11-20T14:30:00+00:00"),
            self._row("2026-11-21T14:30:00+00:00", bar_end="2026-11-21T14:35:00+00:00"),
            self._row("2026-11-22T14:30:00+00:00", bar_end="2026-11-22T14:35:00+00:00"),
        ])
        with self.assertRaisesRegex(InsufficientScoredDataError, "3 scored sessions remain"):
            inspect_scored_data(
                root, interval_seconds=300, warmup_sessions=0, min_scored_sessions=20,
                profile_map={"SIM-EQ": "US_EQUITIES"},
            )

    def test_scored_session_coverage_reports_warmup_and_scored_counts(self) -> None:
        root = self._dataset(rows=[
            self._row("2026-11-20T14:30:00+00:00"),
            self._row("2026-11-21T14:30:00+00:00", bar_end="2026-11-21T14:35:00+00:00"),
            self._row("2026-11-22T14:30:00+00:00", bar_end="2026-11-22T14:35:00+00:00"),
        ])
        coverage = inspect_scored_data(
            root, interval_seconds=300, warmup_sessions=1, min_scored_sessions=2,
            profile_map={"SIM-EQ": "US_EQUITIES"},
        )
        self.assertEqual(coverage["SIM-EQ"]["available_sessions"], 3)
        self.assertEqual(coverage["SIM-EQ"]["scored_sessions"], 2)

    def test_warmup_consuming_entire_dataset_is_rejected(self) -> None:
        root = self._dataset(rows=[
            self._row("2026-11-20T14:30:00+00:00"),
            self._row("2026-11-21T14:30:00+00:00", bar_end="2026-11-21T14:35:00+00:00"),
        ])
        with self.assertRaisesRegex(InsufficientScoredDataError, "at least 3 required"):
            inspect_scored_data(
                root, interval_seconds=300, warmup_sessions=2, min_scored_sessions=1,
                profile_map={"SIM-EQ": "US_EQUITIES"},
            )

    def test_run_dataset_fails_before_loading_strategy_for_short_history(self) -> None:
        root = self._dataset(rows=[self._row()])
        with self.assertRaises(InsufficientScoredDataError):
            run_dataset(
                root, interval_seconds=300, warmup_sessions=0, min_scored_sessions=20,
                profile_map={"SIM-EQ": "US_EQUITIES"},
            )

    def test_warmup_dump_does_not_silently_truncate_requested_sessions(self) -> None:
        root = self._dataset(rows=[self._row()])
        output = Path(tempfile.mkdtemp()) / "warmup"
        with self.assertRaisesRegex(InsufficientScoredDataError, "only 1 are available"):
            dump_synthetic_warmup(
                root, output, interval_seconds=300, warmup_sessions=15,
                profile_map={"SIM-EQ": "US_EQUITIES"},
            )
        self.assertFalse(output.exists())

    def test_causal_window_rejects_future_bar(self) -> None:
        t = datetime(2026, 11, 20, 14, 35, tzinfo=UTC)
        bar = Bar(t, t + timedelta(minutes=5), Decimal(100), Decimal(101),
                  Decimal(99), Decimal(100), Decimal(1000), 10)
        self.assertEqual(len(validate_causal_window([bar], t)), 1)

    def test_next_bar_execution_is_strictly_after_decision_close(self) -> None:
        t = datetime(2026, 11, 20, 14, 35, tzinfo=UTC)
        decision = Bar(t - timedelta(minutes=5), t, Decimal(100), Decimal(101),
                       Decimal(99), Decimal(100), Decimal(1000), 10)
        same = Bar(t - timedelta(minutes=5), t, Decimal(100), Decimal(101),
                   Decimal(99), Decimal(100), Decimal(1000), 10)
        next_bar = Bar(t, t + timedelta(minutes=5), Decimal(100), Decimal(101),
                       Decimal(99), Decimal(100), Decimal(1000), 10)
        self.assertEqual(len(validate_next_bar_execution(decision, same)), 1)
        self.assertEqual(validate_next_bar_execution(decision, next_bar), ())
