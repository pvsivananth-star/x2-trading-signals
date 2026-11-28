"""Environment-based local configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path\n\nfrom .domain.runtime import RuntimeMode


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str | None
    archive_root: Path
    migrations_dir: Path
    provider: str = "generic"
    venue_symbol: str | None = None
    canonical_symbol: str | None = None

    @classmethod
    def from_env(cls) -> Settings:
        mode = os.getenv("TRADING_MODE", "demo").strip().lower()
        if mode not in {"backtest", "demo", "offline", "prod"}:
            raise ValueError("initial live broker mode is rejected; use backtest, demo, offline, or prod")
        if mode == "prod" and os.getenv("X2_LIVE_BROKER_PHASE") != "2":
            raise ValueError("prod live broker connection is phase 2 and is not enabled")
        provider = os.getenv("DATA_PROVIDER", "generic").strip().lower()
        if not provider:
            raise ValueError("DATA_PROVIDER is required")
        venue_symbol = os.getenv("VENUE_SYMBOL") or None
        canonical_symbol = os.getenv("CANONICAL_SYMBOL") or None
        return cls(
            mode=RuntimeMode.PAPER if mode == "demo" else RuntimeMode(mode if mode != "prod" else "live_disabled"),\n            database_url=os.getenv("DATABASE_URL") or None,
            archive_root=Path(os.getenv("ARCHIVE_ROOT", "./data")),
            migrations_dir=Path(os.getenv("MIGRATIONS_DIR", "./migrations")),
            provider=provider,
            venue_symbol=venue_symbol,
            canonical_symbol=canonical_symbol,
        )
