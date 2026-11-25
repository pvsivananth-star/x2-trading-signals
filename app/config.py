"""Environment-based local configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str | None
    archive_root: Path
    migrations_dir: Path
    provider: str = "binance"
    venue_symbol: str = "BTCUSDT"
    canonical_symbol: str = "BTC-USDT"

    @classmethod
    def from_env(cls) -> Settings:
        mode = os.getenv("TRADING_MODE", "demo").strip().lower()
        if mode not in {"demo", "offline"}:
            raise ValueError("initial local implementation rejects live broker mode")
        provider = os.getenv("DATA_PROVIDER", "binance").strip().lower()
        if not provider:
            raise ValueError("DATA_PROVIDER is required")
        venue_symbol = os.getenv("VENUE_SYMBOL", "BTCUSDT").strip()
        canonical_symbol = os.getenv("CANONICAL_SYMBOL", "BTC-USDT").strip()
        if not venue_symbol or not canonical_symbol:
            raise ValueError("venue and canonical symbols are required")
        return cls(
            database_url=os.getenv("DATABASE_URL") or None,
            archive_root=Path(os.getenv("ARCHIVE_ROOT", "./data")),
            migrations_dir=Path(os.getenv("MIGRATIONS_DIR", "./migrations")),
            provider=provider,
            venue_symbol=venue_symbol,
            canonical_symbol=canonical_symbol,
        )
