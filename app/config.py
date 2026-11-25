"""Environment-based local configuration."""

from __future__ import annotations

from dataclasses import dataclass
import os
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
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv("DATABASE_URL") or None,
            archive_root=Path(os.getenv("ARCHIVE_ROOT", "./data")),
            migrations_dir=Path(os.getenv("MIGRATIONS_DIR", "./migrations")),
            provider=os.getenv("DATA_PROVIDER", "binance"),
            venue_symbol=os.getenv("VENUE_SYMBOL", "BTCUSDT"),
            canonical_symbol=os.getenv("CANONICAL_SYMBOL", "BTC-USDT"),
        )
