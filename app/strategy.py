"""Confluence_v1 signal engine. Uses only causal FeatureSnapshot inputs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.features import FeatureSnapshot
from app.policy import TradingPolicy
from app.regime import Regime


def _decimal_text(value: Decimal | None) -> str:
    return "N/A" if value is None else format(value.normalize(), "f")


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
    display_action: str = "HOLD"
    parameters: Mapping[str, str] = field(default_factory=dict)
    reason: str = ""


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
        try:
            _market_name, market = self.policy.resolve_market(symbol)
            slot_rvol = Decimal(str(market["slot_rvol_min"]))
            required_room = Decimal(str(market["min_net_moveroom_pct"]))
        except (ValueError, KeyError):
            slot_rvol = None
            required_room = None
        parameters = {
            "trend": snapshot.trend,
            "rvol": _decimal_text(snapshot.rvol),
            "rvol_threshold": _decimal_text(slot_rvol),
            "fib_382": _decimal_text(snapshot.fib_382),
            "fib_618": _decimal_text(snapshot.fib_618),
            "move_room": _decimal_text(snapshot.move_room),
            "move_room_threshold": _decimal_text(required_room),
            "stale_seconds": _decimal_text(Decimal(str(stale_feed_seconds))),
            "stale_limit": _decimal_text(Decimal(str(stale_threshold_seconds))),
        }
        if action == "enter_long":
            display_action = "BUY"
            reason = (
                f"BUY {symbol}: trend is up (RVOL {_decimal_text(snapshot.rvol)} >= {_decimal_text(slot_rvol)} "
                f"and MoveRoom {_decimal_text(snapshot.move_room)} meets {_decimal_text(required_room)})"
            )
        elif action == "exit_long":
            display_action = "SELL"
            reason = (
                f"SELL {symbol}: trend/structure turned down ({snapshot.trend}, {snapshot.structure})"
            )
        else:
            display_action = "HOLD"
            if gates:
                reason = f"HOLD {symbol}: blocked by {', '.join(gates)}"
            elif snapshot.trend != "up":
                reason = f"HOLD {symbol}: trend is {snapshot.trend}, not an uptrend"
            elif snapshot.rvol is None:
                reason = f"HOLD {symbol}: RVOL is unavailable"
            else:
                reason = (
                    f"HOLD {symbol}: RVOL {_decimal_text(snapshot.rvol)} < {_decimal_text(slot_rvol)} "
                    f"or MoveRoom {_decimal_text(snapshot.move_room)} does not meet {_decimal_text(required_room)}"
                )
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
            timestamp=timestamp,
            symbol=symbol,
            action=action,
            inputs=inputs,
            rejected_gates=tuple(dict.fromkeys(gates)),
            selected_fib_zone=zone,
            expected_move_room=snapshot.move_room,
            config_version=self.policy.config_version,
            signal_id=digest,
            dedupe_key=dedupe,
            display_action=display_action,
            parameters=parameters,
            reason=reason,
        )
