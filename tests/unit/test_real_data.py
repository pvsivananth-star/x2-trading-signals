"""Loader tests. Synthetic inline rows always run; real-data checks run only when X2_REAL_DATA_DIR is set."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.real_data import (
    BARS_FILE,
    REAL_DATA_ENV,
    REPO_ROOT,
    RealDataError,
    group_bars,
    load_bar_records,
    load_real_bars,
    real_data_dir,
)

HEADER = ("instrument_id,interval_seconds,bar_start,bar_end,open_price,high_price,low_price,"
          "close_price,volume,trade_count,vwap,is_complete,quality_status,source_run_id,created_at,updated_at\n")
GOOD = "1,60,2026-11-19 14:30:00+00,2026-11-19 14:31:00+00,352.91,353.32,352.91,353.32,151473,3,353.06,true,complete,r,c,u\n"


def _write(directory: str, body: str, name: str = BARS_FILE) -> Path:
    path = Path(directory) / name
    path.write_text(HEADER + body, encoding="utf-8")
    return path


class LoaderSyntheticTests(unittest.TestCase):
    def test_valid_rows_load_and_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, GOOD + GOOD.replace("14:30:00", "14:31:00").replace("14:31:00+00,2026-11-19 14:31:00", "14:31:00+00,2026-11-19 14:32:00"))
            grouped = group_bars(load_bar_records(path))
        self.assertEqual(list(grouped), [(1, 60)])
        self.assertEqual(len(grouped[(1, 60)]), 2)

    def test_bad_ohlc_reports_row_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = GOOD.replace("353.32,352.91,353.32", "352.00,352.91,353.32")  # high below close
            with self.assertRaisesRegex(RealDataError, "row 2: OHLC bounds"):
                load_bar_records(_write(tmp, bad))

    def test_bar_end_must_match_interval(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = GOOD.replace("2026-11-19 14:31:00+00", "2026-11-19 14:35:00+00", 1)
            with self.assertRaisesRegex(RealDataError, "bar_end is not bar_start"):
                load_bar_records(_write(tmp, bad))

    def test_missing_column_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / BARS_FILE
            path.write_text("instrument_id,interval_seconds\n1,60\n", encoding="utf-8")
            with self.assertRaisesRegex(RealDataError, "missing columns"):
                load_bar_records(path)

    def test_data_dir_inside_repo_is_refused(self):
        with mock.patch.dict(os.environ, {REAL_DATA_ENV: str(REPO_ROOT / "data")}), \
                self.assertRaisesRegex(RealDataError, "inside the repository"):
            real_data_dir()

    def test_unset_environment_returns_none(self):
        with mock.patch.dict(os.environ, {}, clear=False), self.subTest("unset"):
            os.environ.pop(REAL_DATA_ENV, None)
            self.assertIsNone(real_data_dir())
            self.assertIsNone(load_real_bars())


@unittest.skipUnless(os.environ.get(REAL_DATA_ENV), f"set {REAL_DATA_ENV} to run real-data checks")
class LoaderRealDataTests(unittest.TestCase):
    """Runs only on a machine that holds the licensed files outside the repository."""

    @classmethod
    def setUpClass(cls):
        cls.records = load_bar_records(Path(real_data_dir()) / BARS_FILE)
        cls.grouped = group_bars(cls.records)

    def test_schema_and_row_count(self):
        self.assertEqual(len(self.records), 14060)

    def test_expected_instrument_interval_pairs(self):
        self.assertEqual(
            sorted(self.grouped),
            [(1, 60), (1, 300), (1, 900), (2, 60), (2, 300), (2, 900), (3, 60), (3, 300), (3, 900)],
        )

    def test_equity_regular_session_minute_count(self):
        # 5 regular sessions x 390 one-minute bars
        self.assertEqual(len(self.grouped[(1, 60)]), 1950)
        self.assertEqual(len(self.grouped[(2, 60)]), 1950)


if __name__ == "__main__":
    unittest.main()
