"""Confluence_v1 signal engine. Uses only causal FeatureSnapshot inputs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.features import FeatureSnapshot
from app.policy import TradingPolicy
from app.regime import Regime


@dataclass(frozen=True, slots=True)
class DecisionCard:
    timestamp: datetime
    symbol: str
    action: str
    inputs: Mapping[str, Any]
    rejected_gates: tuple[str, ...]
    selected_fib_zone: str | None
    expected_move_room: Decimal | None
    config_version: str
    signal_id: str
    dedupe_key: str


class ConfluenceV1:
    def __init__(self, strategy_config: Mapping[str, Any], policy: TradingPolicy) -> None:
        if not isinstance(strategy_config, Mapping) or "strategy" not in strategy_config:
            raise ValueError("missing required strategy config")
        self.strategy = strategy_config["strategy"]
        if self.strategy.get("id") != "confluence_pullback_v1":
            raise ValueError("unknown strategy id")
        if self.strategy.get("enabled") is not True:
            raise ValueError("strategy must be explicitly enabled")
        allowed = set(self.strategy.get("allowed_regimes", []))
        if not allowed or not allowed.issubset({r.value for r in Regime}):
            raise ValueError("missing or invalid allowed regimes")
        tf = self.strategy.get("timeframes")
        if not isinstance(tf, Mapping) or {5, 15, 30}.isdisjoint(
            {tf.get("signal_mins"), tf.get("swing_mins"), tf.get("discovery_mins")}
        ):
            raise ValueError("configured timeframes must include 5/15/30")
        self.policy = policy

    @classmethod
    def from_yaml(cls, path: str | Path, policy: TradingPolicy) -> ConfluenceV1:
        import yaml

        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls(data, policy)

    def evaluate(
        self,
        *,
        timestamp: datetime,
        symbol: str,
        snapshot: FeatureSnapshot,
        regime: Regime,
        stale_feed_seconds: float,
        stale_threshold_seconds: float,
        session_open: bool = True,
        minutes_to_close: int | None = None,
        position_quantity: Decimal = Decimal(0),
        live_mode: bool = False,
    ) -> DecisionCard:
        policy = self.policy.evaluate(
            timestamp=timestamp,
            symbol=symbol,
            regime=regime,
            stale_feed_seconds=stale_feed_seconds,
            stale_threshold_seconds=stale_threshold_seconds,
            session_open=session_open,
            minutes_to_close=minutes_to_close,
            live_mode=live_mode,
        )
        gates = list(policy.rejected_gates)
        if regime.value not in self.strategy.get("allowed_regimes", []):
            gates.append("regime_not_allowed")

        zone: str | None = None
        action = "hold"
        if snapshot.trend == "up" and snapshot.rvol is not None and policy.allowed:
            market = self.policy.resolve_market(symbol)[1]
            if snapshot.rvol >= Decimal(str(market["slot_rvol_min"])):
                sufficient_room = snapshot.move_room is not None
                if snapshot.move_room is not None:
                    required_room = Decimal(str(market["min_net_moveroom_pct"]))
                    sufficient_room = abs(snapshot.move_room) >= required_room
                    if not sufficient_room:
                        gates.append("insufficient_move_room")
                if snapshot.fib_382 is not None and snapshot.fib_618 is not None:
                    zone = "CORE_382_618"
                    if position_quantity == 0 and policy.allowed and sufficient_room:
                        action = "enter_long"
        if position_quantity != 0 and (
            snapshot.trend == "down" or snapshot.structure in {"lower-highs-lower-lows"}
        ):
            action = "exit_long"
            zone = (
                "TRENDLINE_BREAK"
                if snapshot.structure in {"lower-highs-lower-lows"}
                else "CONFIRMED_BREAK"
            )

        inputs = {
            "trend": snapshot.trend,
            "structure": snapshot.structure,
            "rvol": str(snapshot.rvol),
            "atr": str(snapshot.atr),
            "fib_382": str(snapshot.fib_382),
            "fib_618": str(snapshot.fib_618),
            "move_room": str(snapshot.move_room),
            "regime": regime.value,
            "stale_feed_seconds": stale_feed_seconds,
        }
        payload = json.dumps(
            {
                "timestamp": timestamp.isoformat(),
                "symbol": symbol,
                "action": action,
                "inputs": inputs,
                "config": self.policy.config_version,
            },
            sort_keys=True,
            default=str,
        )
        digest = hashlib.sha256(payload.encode()).hexdigest()
        dedupe = f"{symbol}:{timestamp.isoformat()}:{action}:{digest[:16]}"
        return DecisionCard(
            timestamp,
            symbol,
            action,
            inputs,
            tuple(dict.fromkeys(gates)),
            zone,
            snapshot.move_room,
            self.policy.config_version,
            digest,
            dedupe,
        )
