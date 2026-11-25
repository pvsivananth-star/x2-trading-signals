import os
import unittest
from datetime import UTC, datetime
from decimal import Decimal

from app.config import Settings
from app.domain.events import RawQuoteEvent


class Phase0to3Tests(unittest.TestCase):
    def test_live_broker_mode_is_rejected(self) -> None:
        previous = os.environ.get("TRADING_MODE")
        os.environ["TRADING_MODE"] = "live"
        try:
            with self.assertRaisesRegex(ValueError, "live broker"):
                Settings.from_env()
        finally:
            if previous is None:
                os.environ.pop("TRADING_MODE", None)
            else:
                os.environ["TRADING_MODE"] = previous

    def test_quote_contract_validates_book(self) -> None:
        quote = RawQuoteEvent(
            venue_id="binance",
            venue_symbol="BTCUSDT",
            event_time=datetime(2026, 11, 20, tzinfo=UTC),
            received_time=datetime(2026, 11, 20, tzinfo=UTC),
            bid_price=Decimal(100),
            bid_quantity=Decimal(2),
            ask_price=Decimal(101),
            ask_quantity=Decimal(3),
        )
        self.assertEqual(quote.deduplication_key.startswith("quote-payload:"), True)

    def test_multi_asset_fixture_covers_required_categories(self) -> None:
        fixture_path = (
            os.path.join("data", "fixtures", "multi_asset", "5-day-2026-11-20-to-2026-11-24")
        )
        import json

        with open(os.path.join(fixture_path, "manifest.json"), encoding="utf-8") as source:
            manifest = json.load(source)

        categories = {details["category"] for details in manifest["symbols"].values()}
        self.assertEqual(categories, {"eq", "etf", "index", "gold", "oil", "crypto"})

    def test_crossed_quote_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "bid_price"):
            RawQuoteEvent(
                venue_id="binance",
                venue_symbol="BTCUSDT",
                event_time=datetime(2026, 11, 20, tzinfo=UTC),
                received_time=datetime(2026, 11, 20, tzinfo=UTC),
                bid_price=Decimal(102),
                ask_price=Decimal(101),
            )


if __name__ == "__main__":
    unittest.main()
