import unittest

from app.providers.registry import PUBLIC_SOURCES, resolve_source


class SourceRegistryTests(unittest.TestCase):
    def test_resolves_registered_sources_for_all_feed_types(self) -> None:
        self.assertEqual(resolve_source("binance").asset_class, "crypto")
        self.assertEqual(resolve_source("yahoo_chart").feed_type, "ohlc_rest")
        self.assertEqual(resolve_source("stooq").feed_type, "historical_dump")
        self.assertEqual(resolve_source("google_finance").provider, "google_finance")

    def test_every_registered_source_is_unambiguous_and_license_recorded(self) -> None:
        for source in PUBLIC_SOURCES.values():
            self.assertTrue(source.provider)
            self.assertTrue(source.venue_id)
            self.assertTrue(source.endpoint.startswith("https://"))
            self.assertTrue(source.license_reference)

    def test_unknown_source_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown source provider"):
            resolve_source("not-a-provider")


if __name__ == "__main__":
    unittest.main()
