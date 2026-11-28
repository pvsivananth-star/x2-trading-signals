"""Market-data application boundary over the existing FeedStore."""
from __future__ import annotations
from datetime import datetime
from typing import Sequence
from app.domain.ports import MarketDataPort
from app.feeds import FeedStore, Candle

class FeedMarketDataAdapter(MarketDataPort):
    def __init__(self, store: FeedStore) -> None:
        self.store = store
    def subscribe(self, instruments: Sequence[str]) -> None:
        return None
    def snapshot(self, instrument: str, as_of: datetime) -> dict[str, list[Candle]]:
        return {tf: self.store.bars(instrument, tf, 1000) for tf in ("1m","5m","15m","30m","1d","1W")}
