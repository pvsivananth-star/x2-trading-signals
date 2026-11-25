"""Binance public historical-trade and WebSocket payload parsing.

This module intentionally does not download data or open sockets. The importer and
collector layers can supply files/messages, keeping provider parsing testable and
preventing network access from leaking into strategy code.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import gzip
import io
from pathlib import Path
from typing import Any, Iterator, TextIO
import zipfile

from app.domain.events import RawTradeEvent, epoch_millis_to_utc, decimal_value


UTC = timezone.utc


class BinancePublicTradeAdapter:
    """Parse Binance public trade-dump rows and public trade-stream messages."""

    venue_id = "binance"

    def __init__(self, venue_symbol: str = "BTCUSDT") -> None:
        self.venue_symbol = venue_symbol

    def iter_dump_rows(self, path: Path) -> Iterator[dict[str, Any] | list[str]]:
        return iter_binance_dump_rows(path, expected_symbol=self.venue_symbol)

    def parse_dump_row(self, row: dict[str, Any] | list[str], received_time: datetime) -> RawTradeEvent:
        """Parse header-based or positional Binance historical trade rows.

        Supported positional layout is Binance's common historical trade format:
        ``id, price, qty, quote_qty, time, is_buyer_maker, is_best_match``.
        """

        if isinstance(row, dict):
            row = {str(key).lstrip(chr(0xFEFF)).strip().lower(): value for key, value in row.items()}
            explicit_symbol = _first(row, "symbol", "s")
            if explicit_symbol and str(explicit_symbol).upper() != self.venue_symbol.upper():
                raise ValueError(
                    f"unexpected Binance symbol {explicit_symbol}; expected {self.venue_symbol}"
                )
            trade_id = _first(row, "id", "trade_id", "tradeid")
            price = _required(row, "price")
            quantity = _first(row, "qty", "quantity")
            event_time = _first(row, "time", "timestamp", "t")
            buyer_maker = _first(row, "is_buyer_maker", "isbuyermaker", "m")
            payload = dict(row)
        else:
            if len(row) < 6:
                raise ValueError(f"historical trade row has {len(row)} columns; expected at least 6")
            trade_id, price, quantity, _quote_quantity, event_time, buyer_maker = row[:6]
            payload = {"columns": list(row)}

        if trade_id in (None, ""):
            raise ValueError("historical trade row is missing trade ID")
        if quantity in (None, ""):
            raise ValueError("historical trade row is missing quantity")
        if event_time in (None, ""):
            raise ValueError("historical trade row is missing event time")

        return RawTradeEvent(
            venue_id=self.venue_id,
            venue_symbol=self.venue_symbol,
            event_time=_parse_binance_time(event_time),
            received_time=received_time,
            price=decimal_value(price, "price"),
            quantity=decimal_value(quantity, "quantity"),
            venue_trade_id=str(trade_id),
            aggressor_side=_aggressor_side(buyer_maker),
            payload=payload,
        )

    def parse_websocket_message(self, message: dict[str, Any], received_time: datetime) -> RawTradeEvent:
        """Parse a Binance ``trade`` event from the public WebSocket stream."""

        if message.get("e") != "trade":
            raise ValueError(f"unsupported Binance event type: {message.get('e')!r}")
        if not message.get("s"):
            raise ValueError("WebSocket trade message is missing symbol")
        symbol = str(message["s"]).upper()
        if symbol != self.venue_symbol.upper():
            raise ValueError(f"unexpected Binance symbol {symbol}; expected {self.venue_symbol}")
        trade_id = message.get("t")
        if trade_id is None:
            raise ValueError("WebSocket trade message is missing trade ID")
        return RawTradeEvent(
            venue_id=self.venue_id,
            venue_symbol=symbol,
            event_time=_parse_binance_time(message.get("T")),
            received_time=received_time,
            price=decimal_value(message.get("p"), "price"),
            quantity=decimal_value(message.get("q"), "quantity"),
            venue_trade_id=str(trade_id),
            aggressor_side=_aggressor_side(message.get("m")),
            payload=dict(message),
        )


def iter_binance_dump_rows(
    path: Path, *, expected_symbol: str | None = None
) -> Iterator[dict[str, Any] | list[str]]:
    """Yield only trade CSV rows for the configured symbol."""

    suffixes = {suffix.lower() for suffix in path.suffixes}
    is_zip = ".zip" in suffixes or zipfile.is_zipfile(path)
    is_gzip = ".gz" in suffixes or _has_gzip_magic(path)
    if is_zip:
        with zipfile.ZipFile(path) as archive:
            members = [
                info
                for info in archive.infolist()
                if not info.is_dir() and Path(info.filename).suffix.lower() == ".csv"
            ]
            if not members:
                raise ValueError("historical dump ZIP contains no CSV files")
            if expected_symbol:
                matching = [
                    info
                    for info in members
                    if expected_symbol.upper() in Path(info.filename).name.upper()
                ]
                if matching:
                    members = matching
                else:
                    raise ValueError(
                        f"ZIP contains no uniquely identifiable {expected_symbol} trade CSV"
                    )
            for info in members:
                with archive.open(info, "r") as binary_stream:
                    with io.TextIOWrapper(
                        binary_stream, encoding="utf-8-sig", newline=""
                    ) as text_stream:
                        yield from _iter_csv_stream(text_stream)
        return

    if ".gz" in suffixes:
        with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as text_stream:
            yield from _iter_csv_stream(text_stream)
        return

    with path.open("r", encoding="utf-8-sig", newline="") as text_stream:
        yield from _iter_csv_stream(text_stream)


def _has_gzip_magic(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            return stream.read(2) == bytes((0x1F, 0x8B))
    except OSError:
        return False


def _iter_csv_stream(stream: TextIO) -> Iterator[dict[str, Any] | list[str]]:
    """Detect a header row without assuming every Binance dump has one."""

    reader = csv.reader(stream)
    first = next(reader, None)
    if first is None:
        return
    normalized = {value.strip().lower() for value in first}
    header_tokens = {"id", "trade_id", "price", "qty", "quantity", "time", "timestamp"}
    if normalized.intersection(header_tokens) and "price" in normalized:
        headers = [value.strip().lower() for value in first]
        for row in reader:
            if any(cell.strip() for cell in row):
                yield dict(zip(headers, row, strict=False))
        return

    if any(cell.strip() for cell in first):
        yield first
    for row in reader:
        if any(cell.strip() for cell in row):
            yield row


def _first(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    return None


def _required(row: dict[str, Any], name: str) -> Any:
    value = _first(row, name)
    if value in (None, ""):
        raise ValueError(f"historical trade row is missing {name}")
    return value


def _parse_binance_time(value: Any) -> datetime:
    if value is None:
        raise ValueError("event time is required")
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if "T" in text or "+" in text:
        parsed = datetime.fromisoformat(text)
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return epoch_millis_to_utc(text)


def _aggressor_side(buyer_is_maker: Any) -> str:
    if isinstance(buyer_is_maker, str):
        normalized = buyer_is_maker.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return "sell"
        if normalized in {"false", "0", "no"}:
            return "buy"
    elif buyer_is_maker is True:
        return "sell"
    elif buyer_is_maker is False:
        return "buy"
    return "unknown"
