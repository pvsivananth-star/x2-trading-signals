import unittest
from datetime import UTC, datetime
from decimal import Decimal

from app.domain.events import RawTradeEvent, epoch_millis_to_utc


class EventContractTests(unittest.TestCase):
    def test_epoch_milliseconds_are_stored_as_utc(self) -> None:
        timestamp = epoch_millis_to_utc("1704067200123")
        self.assertEqual(timestamp, datetime(2024, 1, 1, 0, 0, 0, 123000, tzinfo=UTC))

    def test_deduplication_prefers_venue_trade_id(self) -> None:
        event = RawTradeEvent(
            venue_id="binance",
            venue_symbol="BTCUSDT",
            event_time=datetime(2024, 1, 1, tzinfo=UTC),
            received_time=datetime(2024, 1, 1, 0, 0, 1, tzinfo=UTC),
            price=Decimal("42000.10"),
            quantity=Decimal("0.001"),
            venue_trade_id="123",
            payload={"id": 123, "price": "42000.10"},
        )
        self.assertEqual(event.deduplication_key, "trade:binance:123")

    def test_payload_deduplication_is_stable_when_no_id_exists(self) -> None:
        kwargs = {
            "venue_id": "provider",
            "venue_symbol": "BTCUSDT",
            "event_time": datetime(2024, 1, 1, tzinfo=UTC),
            "received_time": datetime(2024, 1, 1, tzinfo=UTC),
            "price": Decimal(10),
            "quantity": Decimal(2),
            "payload": {"b": 2, "a": 1},
        }
        self.assertEqual(RawTradeEvent(**kwargs).deduplication_key, RawTradeEvent(**kwargs).deduplication_key)

    def test_naive_timestamp_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone"):
            RawTradeEvent(
                venue_id="binance",
                venue_symbol="BTCUSDT",
                event_time=datetime(2024, 1, 1),
                received_time=datetime(2024, 1, 1, tzinfo=UTC),
                price=Decimal(1),
                quantity=Decimal(1),
            )

    def test_float_price_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "float"):
            RawTradeEvent(
                venue_id="binance",
                venue_symbol="BTCUSDT",
                event_time=datetime(2024, 1, 1, tzinfo=UTC),
                received_time=datetime(2024, 1, 1, tzinfo=UTC),
                price=0.1,
                quantity=Decimal(1),
            )

    def test_non_positive_price_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive"):
            RawTradeEvent(
                venue_id="binance",
                venue_symbol="BTCUSDT",
                event_time=datetime(2024, 1, 1, tzinfo=UTC),
                received_time=datetime(2024, 1, 1, tzinfo=UTC),
                price=Decimal(0),
                quantity=Decimal(1),
            )


if __name__ == "__main__":
    unittest.main()
