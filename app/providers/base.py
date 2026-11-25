"""Provider-neutral adapter protocols."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Protocol

from app.domain.events import RawTradeEvent


class HistoricalTradeAdapter(Protocol):
    venue_id: str
    venue_symbol: str

    def iter_dump_rows(self, path: Path) -> Iterator[dict[str, Any] | list[str]]: ...

    def parse_dump_row(self, row: dict[str, Any] | list[str], received_time: datetime) -> RawTradeEvent: ...


class LiveTradeAdapter(HistoricalTradeAdapter, Protocol):
    def parse_websocket_message(self, message: dict[str, Any], received_time: datetime) -> RawTradeEvent: ...
