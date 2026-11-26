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

# Reaction zones from config/strategies/confluence_pullback.yaml, expressed as pullback depth
# (retracement of the window range from its high). Each entry: (min, max, size_weight, requirement).
REACTION_ZONES: dict[str, tuple[Decimal, Decimal, Decimal, str | None]] = {
    "CORE_382_618": (Decimal("0.382"), Decimal("0.618"), Decimal("1.00"), None),
    "SHALLOW_VWAP_236_382": (Decimal("0.236"), Decimal("0.382"), Decimal("0.68"), "vwap_hold"),
    "DEEP_RECLAIM_618_786": (Decimal("0.618"), Decimal("0.786"), Decimal("0.68"), "reclaim_close"),
}


def _decimal_text(value: Decimal | None) -> str:
    return "N/A" if value is None else format(value.normalize(), "f")


def classify_reaction_zone(depth: Decimal | None) -> str | None:
    """Return the reaction zone for a pullback depth. Core is checked first; boundaries are inclusive
    for core, and shallow/deep use half-open ranges so adjacent zones never overlap."""
    if depth is None:
        return None
    if Decimal("0.382") <= depth <= Decimal("0.618"):
        return "CORE_382_618"
    if Decimal("0.236") <= depth < Decimal("0.382"):
        return "SHALLOW_VWAP_236_382"
    if Decimal("0.618") < depth <= Decimal("0.786"):
        return "DEEP_RECLAIM_618_786"
    return None


def _requirement_met(requirement: str | None, snapshot: FeatureSnapshot) -> bool:
    if requirement is None:
        return True
    if snapshot.last_close is None or snapshot.vwap is None:
        return False
    # vwap_hold and reclaim_close both require the last close back at or above causal VWAP.
    return snapshot.last_close >= snapshot.vwap


def projected_levels(snapshot: FeatureSnapshot, target_mult: Decimal, stop_mult: Decimal,
                     roundtrip_cost: Decimal, slippage_buffer: Decimal) -> tuple[Decimal, Decimal, Decimal] | None:
    """Return (target, stop, net_move_room_pct) for a long entry at last_close.

    Net MoveRoom = |target - entry| / entry - roundtrip cost - slippage buffer (see review v2).
    """
    if snapshot.atr is None or snapshot.last_close is None or snapshot.last_close <= 0:
        return None
    entry = snapshot.last_close
    target = entry + target_mult * snapshot.atr
    stop = entry - stop_mult * snapshot.atr
    gross = abs(target - entry) / entry
    return target, stop, gross - roundtrip_cost - slippage_buffer


def _trend_broken(snapshot: FeatureSnapshot) -> bool:
    return snapshot.trend == "down" or snapshot.structure == "lower-highs-lower-lows"


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
        mr = self.strategy.get("move_room")
        if not isinstance(mr, Mapping):
            raise ValueError("missing move_room config")
        self.target_mult = Decimal(str(mr["target_atr_mult"]))
        self.stop_mult = Decimal(str(mr["stop_atr_mult"]))
        self.min_rr = Decimal(str(mr["min_reward_to_risk"]))
        self.roundtrip_cost = Decimal(str(mr["roundtrip_cost_pct"]))
        self.slippage_buffer = Decimal(str(mr["slippage_buffer_pct"]))
        if self.stop_mult <= 0 or self.target_mult <= 0:
            raise ValueError("ATR multiples must be positive")
        if self.target_mult / self.stop_mult < self.min_rr:
            raise ValueError("configured target/stop ratio is below minimum reward-to-risk")

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
        previous_snapshot: FeatureSnapshot | None = None,
        position_stop: Decimal | None = None,
        position_target: Decimal | None = None,
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

        market = None
        try:
            _market_name, market = self.policy.resolve_market(symbol)
        except ValueError:
            market = None

        engine = self.policy.config["engine"]
        zone: str | None = None
        size_weight: Decimal | None = None
        sufficient_room = False
        action = "hold"
        flatten_mins = int(engine["mandatory_pre_close_flat_mins"])
        pre_close_flat = minutes_to_close is not None and minutes_to_close <= flatten_mins
        levels = projected_levels(snapshot, self.target_mult, self.stop_mult,
                                  self.roundtrip_cost, self.slippage_buffer)
        required_room = None
        slot_rvol = None
        if market is not None and "min_net_moveroom_pct" in market and "slot_rvol_min" in market:
            required_room = Decimal(str(market["min_net_moveroom_pct"]))
            slot_rvol = Decimal(str(market["slot_rvol_min"]))

        if position_quantity != 0:
            last = snapshot.last_close
            if pre_close_flat:
                action = "exit_long"
                zone = "MANDATORY_PRE_CLOSE_FLAT"
            elif position_stop is not None and last is not None and last <= position_stop:
                action = "exit_long"
                zone = "HARD_STOP"
            elif position_target is not None and last is not None and last >= position_target:
                action = "exit_long"
                zone = "TARGET_HIT"
            elif _trend_broken(snapshot) and previous_snapshot is not None and _trend_broken(previous_snapshot):
                action = "exit_long"
                zone = "TRENDLINE_BREAK" if snapshot.structure == "lower-highs-lower-lows" else "CONFIRMED_BREAK"
        elif snapshot.trend == "up" and policy.allowed and market is not None:
            if required_room is None or slot_rvol is None:
                gates.append("market_thresholds_missing")
            elif snapshot.rvol is None:
                gates.append("rvol_unavailable")
            elif snapshot.rvol < slot_rvol:
                gates.append("rvol_below_threshold")
            if levels is None:
                gates.append("move_room_unavailable")
            elif required_room is not None:
                sufficient_room = levels[2] >= required_room
                if not sufficient_room:
                    gates.append("insufficient_move_room")
            zone = classify_reaction_zone(snapshot.pullback_depth)
            if zone is None:
                gates.append("outside_reaction_zone")
            else:
                _lo, _hi, size_weight, requirement = REACTION_ZONES[zone]
                if not _requirement_met(requirement, snapshot):
                    gates.append(f"{requirement}_not_met")
                    zone = None
            if zone is not None and sufficient_room and "rvol_below_threshold" not in gates \
                    and "rvol_unavailable" not in gates:
                action = "enter_long"

        inputs = {
            "trend": snapshot.trend,
            "structure": snapshot.structure,
            "rvol": str(snapshot.rvol),
            "atr": str(snapshot.atr),
            "vwap": str(snapshot.vwap),
            "last_close": str(snapshot.last_close),
            "pullback_depth": str(snapshot.pullback_depth),
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
            "pullback_depth": _decimal_text(snapshot.pullback_depth),
            "zone": zone or "N/A",
            "size_weight": _decimal_text(size_weight),
            "move_room": _decimal_text(snapshot.move_room),
            "move_room_threshold": _decimal_text(required_room),
            "max_risk_pct": "N/A" if policy.max_risk_pct is None else str(policy.max_risk_pct),
            "stale_seconds": _decimal_text(Decimal(str(stale_feed_seconds))),
            "stale_limit": _decimal_text(Decimal(str(stale_threshold_seconds))),
        }
        if action == "enter_long":
            display_action = "BUY"
            reason = (
                f"BUY {symbol}: trend is up (RVOL {_decimal_text(snapshot.rvol)} >= {_decimal_text(slot_rvol)}, "
                f"pullback {_decimal_text(snapshot.pullback_depth)} in {zone}, "
                f"MoveRoom {_decimal_text(snapshot.move_room)} meets {_decimal_text(required_room)})"
            )
        elif action == "exit_long":
            display_action = "SELL"
            if zone == "MANDATORY_PRE_CLOSE_FLAT":
                reason = f"SELL {symbol}: mandatory pre-close flat ({minutes_to_close} minutes to close)"
            else:
                reason = f"SELL {symbol}: confirmed trend break ({snapshot.trend}, {snapshot.structure})"
        else:
            display_action = "HOLD"
            if gates:
                reason = f"HOLD {symbol}: blocked by {', '.join(gates)}"
            elif snapshot.trend != "up":
                reason = f"HOLD {symbol}: trend is {snapshot.trend}, not an uptrend"
            elif snapshot.rvol is None:
                reason = f"HOLD {symbol}: RVOL is unavailable"
            elif snapshot.move_room is not None and snapshot.move_room < 0:
                reason = (
                    f"HOLD {symbol}: MoveRoom {_decimal_text(snapshot.move_room)} is negative, "
                    f"so downside is not smaller than upside; required {_decimal_text(required_room)}"
                )
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
