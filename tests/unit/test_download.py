from pathlib import Path
import tempfile
import unittest

from app.ingestion.download import DownloadError, materialize_source


class DownloadTests(unittest.TestCase):
    def test_local_source_is_returned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "trades.zip"
            source.write_bytes(b"fixture")
            self.assertEqual(materialize_source(str(source), Path(directory) / "downloads"), source)

    def test_non_allowlisted_url_is_rejected(self) -> None:
        with self.assertRaisesRegex(DownloadError, "allow-listed"):
            materialize_source("http://example.invalid/trades.zip", Path("/tmp/x2-download-test"))


if __name__ == "__main__":
    unittest.main()
