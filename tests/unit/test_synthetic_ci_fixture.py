"""Checks the committed synthetic CI fixture: loads through the strict bar loader and is labelled synthetic."""

import hashlib
import json
import unittest
from pathlib import Path

from app.real_data import group_bars, load_bar_records

FIXTURE = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "synthetic_ci"


class SyntheticCiFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((FIXTURE / "manifest.json").read_text())

    def test_manifest_is_labelled_synthetic(self):
        self.assertEqual(self.manifest["source"], "deterministic_synthetic_test_fixture")
        self.assertFalse(self.manifest["contains_real_market_data"])
        self.assertFalse(self.manifest["performance_claims_allowed"])

    def test_file_checksums_match_manifest(self):
        for name, meta in self.manifest["files"].items():
            data = (FIXTURE / name).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), meta["sha256"], name)
            self.assertEqual(len(data), meta["bytes"], name)

    def test_bars_pass_strict_loader(self):
        grouped = group_bars(load_bar_records(FIXTURE / "bars.csv"))
        self.assertEqual(sorted(grouped), [(1, 60), (1, 300), (1, 900), (2, 60), (2, 300), (2, 900)])
        self.assertEqual(len(grouped[(1, 60)]), 1950)
        self.assertEqual(len(grouped[(2, 60)]), 7200)


if __name__ == "__main__":
    unittest.main()
