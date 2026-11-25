from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
import zipfile

from app.providers.binance_public import BinancePublicTradeAdapter, iter_binance_dump_rows


class BinanceProviderTests(unittest.TestCase):
    received_time = datetime(2024, 1, 1, 0, 0, 1, tzinfo=timezone.utc)

    def setUp(self) -> None:
        self.adapter = BinancePublicTradeAdapter("BTCUSDT")

    def test_header_row_is_parsed(self) -> None:
        event = self.adapter.parse_dump_row(
            {"id": "7", "price": "42000.10", "qty": "0.001", "time": "1704067200123", "is_buyer_maker": "false"},
            self.received_time,
        )
        self.assertEqual(event.venue_trade_id, "7")
        self.assertEqual(str(event.price), "42000.10")
        self.assertEqual(event.aggressor_side, "buy")
        self.assertEqual(event.event_time.year, 2024)

    def test_header_names_are_case_insensitive(self) -> None:
        event = self.adapter.parse_dump_row(
            {"ID": "7", "PRICE": "42000.10", "QTY": "0.001", "TIME": "1704067200123", "IS_BUYER_MAKER": "false"},
            self.received_time,
        )
        self.assertEqual(event.venue_trade_id, "7")

    def test_positional_row_is_parsed(self) -> None:
        event = self.adapter.parse_dump_row(
            ["8", "42001.00", "0.002", "84.002", "1704067201123", "true", "true"],
            self.received_time,
        )
        self.assertEqual(event.venue_trade_id, "8")
        self.assertEqual(event.aggressor_side, "sell")

    def test_malformed_websocket_without_type_or_symbol_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "event type"):
            self.adapter.parse_websocket_message(
                {"t": 9, "T": 1704067202123, "p": "42002", "q": "0.003", "m": False},
                self.received_time,
            )

    def test_websocket_without_symbol_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing symbol"):
            self.adapter.parse_websocket_message(
                {"e": "trade", "t": 9, "T": 1704067202123, "p": "42002", "q": "0.003", "m": False},
                self.received_time,
            )

    def test_mismatched_dump_symbol_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unexpected Binance symbol"):
            self.adapter.parse_dump_row(
                {"symbol": "ETHUSDT", "id": "9", "price": "1", "qty": "1", "time": "1704067202123"},
                self.received_time,
            )

    def test_websocket_trade_is_parsed(self) -> None:
        event = self.adapter.parse_websocket_message(
            {"e": "trade", "s": "BTCUSDT", "t": 9, "T": 1704067202123, "p": "42002", "q": "0.003", "m": False},
            self.received_time,
        )
        self.assertEqual(event.venue_trade_id, "9")
        self.assertEqual(event.aggressor_side, "buy")

    def test_zip_dump_rows_are_streamed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trades.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(
                    "BTCUSDT-trades.csv",
                    "id,price,qty,time,is_buyer_maker\n1,10,2,1704067200000,false\n",
                )
            rows = list(iter_binance_dump_rows(path))
        self.assertEqual(rows, [{"id": "1", "price": "10", "qty": "2", "time": "1704067200000", "is_buyer_maker": "false"}])

    def test_utf8_bom_header_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trades.csv"
            path.write_text(
                chr(0xFEFF) + "id,price,qty,time,is_buyer_maker\n1,10,2,1704067200000,false\n",
                encoding="utf-8",
            )
            rows = list(iter_binance_dump_rows(path, expected_symbol="BTCUSDT"))
        self.assertEqual(rows[0]["id"], "1")

    def test_invalid_event_type_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported"):
            self.adapter.parse_websocket_message(
                {"e": "kline", "T": 1704067200000, "t": 1, "p": "1", "q": "1"},
                self.received_time,
            )


if __name__ == "__main__":
    unittest.main()
