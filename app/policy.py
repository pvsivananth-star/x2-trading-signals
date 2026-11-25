"""Versioned, fail-closed trading policy and market/symbol gate resolution."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.regime import Regime


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    rejected_gates: tuple[str, ...]
    max_risk_pct: float | None
    market: str | None
    status: str


class TradingPolicy:
    def __init__(self, config: Mapping[str, Any], config_version: str = "v1") -> None:
        if not isinstance(config, Mapping):
            raise TypeError("policy config must be a mapping")
        self.config = dict(config)
        self.config_version = config_version
        engine = self.config.get("engine")
        markets = self.config.get("markets")
        if not isinstance(engine, Mapping) or not isinstance(markets, Mapping):
            raise TypeError("missing required engine or markets policy")
        for field in (
            "mode",
            "base_currency",
            "pre_close_entry_cutoff_mins",
            "mandatory_pre_close_flat_mins",
        ):
            if field not in engine:
                raise ValueError(f"missing required engine field: {field}")
        if engine["mode"] != "demo":
            raise ValueError("live broker mode is rejected")
        if engine["pre_close_entry_cutoff_mins"] < 0 or engine["mandatory_pre_close_flat_mins"] < 0:
            raise ValueError("negative session cutoff is invalid")
        if engine["mandatory_pre_close_flat_mins"] > engine["pre_close_entry_cutoff_mins"]:
            raise ValueError("conflicting session cutoffs")

    @classmethod
    def from_yaml(cls, path: str | Path) -> TradingPolicy:
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError("PyYAML is required to load policy configuration") from exc
        raw = Path(path).read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
        canonical = json.dumps(data, sort_keys=True, default=str).encode()
        return cls(data, hashlib.sha256(canonical).hexdigest()[:16])

    def resolve_market(self, symbol: str) -> tuple[str, Mapping[str, Any]]:
        if not symbol:
            raise ValueError("symbol is required")
        matches: list[tuple[str, Mapping[str, Any]]] = []
        for name, market in self.config["markets"].items():
            if not isinstance(market, Mapping):
                raise TypeError(f"invalid market config: {name}")
            if symbol in market.get("core_symbols", []):
                matches.append((name, market))
            if symbol in market.get("excluded_symbols", []) or symbol in market.get(
                "excluded_symbols", {}
            ):
                raise ValueError(f"symbol explicitly excluded: {symbol}")
        if len(matches) != 1:
            raise ValueError(f"unknown or conflicting market/symbol status: {symbol}")
        return matches[0]

    def evaluate(
        self,
        *,
        timestamp: datetime,
        symbol: str,
        regime: Regime,
        stale_feed_seconds: float,
        stale_threshold_seconds: float,
        session_open: bool = True,
        minutes_to_close: int | None = None,
        live_mode: bool = False,
    ) -> PolicyDecision:
        if live_mode:
            return PolicyDecision(False, ("live_mode",), None, None, "BLOCKED")
        if not isinstance(regime, Regime):
            return PolicyDecision(False, ("unknown_regime",), None, None, "BLOCKED")
        try:
            market_name, market = self.resolve_market(symbol)
        except ValueError as exc:
            return PolicyDecision(False, (str(exc),), None, None, "BLOCKED")

        rejected: list[str] = []
        tier = str(market.get("tier", ""))
        enabled = market.get("enabled")
        if enabled is not True:
            rejected.append("market_disabled")
        if tier in {"TIER_C_PROBATION", "TIER_D_DISABLED"}:
            rejected.append("probation_or_disabled_market")
        override = market.get("symbol_overrides", {}).get(symbol)
        if isinstance(override, Mapping):
            status = str(override.get("status", "")).upper()
            if status not in {"NORMAL", "PROBATION", "DISABLED", ""}:
                rejected.append("unknown_symbol_status")
            elif status in {"PROBATION", "DISABLED"}:
                rejected.append("probation_or_disabled_symbol")
        if stale_feed_seconds > stale_threshold_seconds:
            rejected.append("stale_feed")
        if not session_open:
            rejected.append("session_closed")

        engine = self.config["engine"]
        if minutes_to_close is not None:
            if minutes_to_close < int(engine["pre_close_entry_cutoff_mins"]):
                rejected.append("entry_cutoff")
            if minutes_to_close <= int(engine["mandatory_pre_close_flat_mins"]):
                rejected.append("mandatory_flatten")

        rules = engine.get("regime_rules", {}).get(regime.value)
        if not isinstance(rules, Mapping):
            rejected.append("unknown_regime_policy")
            max_risk = None
        else:
            max_risk = None
            if rules.get("allow_new_entries") is not True:
                rejected.append("news_or_regime_gating")
            tier_key = "a_tier_risk_pct" if tier.startswith("TIER_A") else "b_tier_risk_pct"
            max_risk = rules.get(tier_key, rules.get("max_risk_pct"))
            override_risk = override.get("max_risk_pct") if isinstance(override, Mapping) else None
            if override_risk is not None:
                max_risk = (
                    min(float(max_risk), float(override_risk))
                    if max_risk is not None
                    else float(override_risk)
                )

        if market_name == "CRYPTO_MAJORS":
            rejected.append("probation_or_disabled_market")
        if market_name not in self.config["markets"]:
            rejected.append("unknown_market")
        return PolicyDecision(
            not rejected,
            tuple(dict.fromkeys(rejected)),
            max_risk,
            market_name,
            "ALLOWED" if not rejected else "BLOCKED",
        )
