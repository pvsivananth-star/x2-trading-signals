"""Provider-neutral source and instrument registration."""

from __future__ import annotations

from dataclasses import dataclass


VALID_ASSET_CLASSES = {
    "equity", "etf", "metal", "future", "forex", "crypto", "option", "bond", "index", "other"
}


@dataclass(frozen=True, slots=True)
class SourceDefinition:
    provider: str
    venue_id: str
    asset_class: str
    source_kind: str
    feed_type: str
    endpoint: str
    license_reference: str
    terms_status: str = "unverified"
    redistribution_allowed: bool | None = None

    def __post_init__(self) -> None:
        if self.asset_class not in VALID_ASSET_CLASSES:
            raise ValueError(f"unsupported asset class: {self.asset_class}")
        if self.source_kind not in {"historical_dump", "rest", "websocket"}:
            raise ValueError(f"unsupported source kind: {self.source_kind}")
        if self.feed_type not in {
            "historical_dump", "tick_rest", "tick_websocket", "ohlc_rest", "rss", "news_api", "calendar", "reference"
        }:
            raise ValueError(f"unsupported feed type: {self.feed_type}")


PUBLIC_SOURCES: dict[str, SourceDefinition] = {
    "binance": SourceDefinition(
        provider="binance", venue_id="binance", asset_class="crypto", source_kind="historical_dump",
        feed_type="historical_dump", endpoint="https://data.binance.vision/",
        license_reference="provider terms pending verification", terms_status="unverified",
    ),
    "yahoo_chart": SourceDefinition(
        provider="yahoo", venue_id="yahoo", asset_class="other", source_kind="rest",
        feed_type="ohlc_rest", endpoint="https://query1.finance.yahoo.com/v8/finance/chart/",
        license_reference="Yahoo/ID compliance and redistribution terms pending verification", terms_status="unverified",
    ),
    "google_finance": SourceDefinition(
        provider="google_finance", venue_id="google_finance", asset_class="other", source_kind="rest",
        feed_type="reference", endpoint="https://www.google.com/finance/",
        license_reference="Google Finance terms and attribution pending verification", terms_status="unverified",
    ),
    "stooq": SourceDefinition(
        provider="stooq", venue_id="stooq", asset_class="other", source_kind="historical_dump",
        feed_type="historical_dump", endpoint="https://stooq.com/db/h/",
        license_reference="Stooq terms and redistribution restrictions pending verification", terms_status="unverified",
    ),
}


def resolve_source(provider: str) -> SourceDefinition:
    try:
        return PUBLIC_SOURCES[provider.lower().strip()]
    except KeyError as exc:
        raise ValueError(f"unknown source provider: {provider}") from exc
