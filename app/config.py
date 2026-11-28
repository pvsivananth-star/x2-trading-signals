"""Environment-based local configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .domain.runtime import RuntimeMode


@dataclass(frozen=True, slots=True)
class Settings:
    mode: RuntimeMode
    database_url: str | None
    archive_root: Path
    migrations_dir: Path
    provider: str = "generic"
    venue_symbol: str | None = None
    canonical_symbol: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        mode_name = os.getenv("TRADING_MODE", "demo").strip().lower()
        if mode_name == "live":
            raise ValueError("live broker mode is rejected; use backtest, demo, offline, or prod")
        if mode_name not in {"backtest", "demo", "offline", "prod"}:
            raise ValueError("initial live broker mode is rejected; use backtest, demo, offline, or prod")
        if mode_name == "prod" and os.getenv("X2_LIVE_BROKER_PHASE") != "2":
            raise ValueError("prod live broker connection is phase 2 and is not enabled")

        mode = RuntimeMode.LIVE_DISABLED if mode_name == "prod" else RuntimeMode(mode_name)
        provider = os.getenv("DATA_PROVIDER", "generic").strip().lower()
        if not provider:
            raise ValueError("DATA_PROVIDER is required")

        return cls(
            mode=mode,
            database_url=os.getenv("DATABASE_URL") or None,
            archive_root=Path(os.getenv("ARCHIVE_ROOT", "./data")),
            migrations_dir=Path(os.getenv("MIGRATIONS_DIR", "./migrations")),
            provider=provider,
            venue_symbol=os.getenv("VENUE_SYMBOL") or None,
            canonical_symbol=os.getenv("CANONICAL_SYMBOL") or None,
        )
