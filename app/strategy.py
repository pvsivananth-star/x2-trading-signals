"""Confluence_v1 signal engine (long and short). Uses only causal FeatureSnapshot inputs.

Side selection: trend == "up" -> long evaluation; trend == "down" -> short evaluation.
Each side mirrors the other: pullback zone, VWAP requirement, structural target, stop, exits.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.event_risk import EventRiskAssessment
from app.features import FeatureSnapshot
from app.policy import TradingPolicy
from app.regime import Regime

# Reaction zones from config/strategies/confluence_pullback.yaml, expressed as pullback depth
# (retracement of the window range against the trend). (min, max, size_weight, requirement).
REACTION_ZONES: dict[str, tuple[Decimal, Decimal, Decimal, str | None]] = {
    "CORE_382_618": (Decimal("0.382"), Decimal("0.618"), Decimal("1.00"), None),
    "SHALLOW_VWAP_236_382": (Decimal("0.236"), Decimal("0.382"), Decimal("0.68"), "vwap_hold"),
    "DEEP_RECLAIM_618_786": (Decimal("0.618"), Decimal("0.786"), Decimal("0.68"), "reclaim_close"),
    # Upgrade 2: shallow momentum continuation, TREND_DAY only, needs an impulse volume pump.
    "MOMENTUM_VWAP_12_236": (Decimal("0.12"), Decimal("0.236"), Decimal("0.75"), "momentum_vwap_hold"),
}

# Volume rules (upgrade 1). Impulse pump on the push, dry-up on the pullback bar.
IMPULSE_RVOL_MIN = Decimal("1.15")
DRY_UP_RVOL_MAX = Decimal("1.15")
# Volume climax exit (upgrade 1): opposing high-volume bar that crosses VWAP against the position.
CLIMAX_RVOL_MIN = Decimal("2.20")
VOLUME_GATES = ("rvol_below_threshold", "rvol_unavailable", "impulse_volume_not_met", "dry_up_not_met")


def _decimal_text(value: Decimal | None) -> str:
    return "N/A" if value is None else format(value.normalize(), "f")


def classify_reaction_zone(
        depth: Decimal | None,
        regime: Regime | None = None,
        impulse_rvol: Decimal | None = None,
        session_efficiency: Decimal | None = None,
        allow_momentum_zone: bool = True,
) -> str | None:
    if depth is None:
        return None
    if (
            allow_momentum_zone
            and Decimal("0.12") <= depth < Decimal("0.236")
            and regime is Regime.TREND_DAY
            and impulse_rvol is not None
            and impulse_rvol >= IMPULSE_RVOL_MIN
            and (session_efficiency is None or session_efficiency >= Decimal("0.55"))
    ):
        return "MOMENTUM_VWAP_12_236"
    if Decimal("0.382") <= depth <= Decimal("0.618"):
        return "CORE_382_618"
    if Decimal("0.236") <= depth < Decimal("0.382"):
        return "SHALLOW_VWAP_236_382"
    if Decimal("0.618") < depth <= Decimal("0.786"):
        return "DEEP_RECLAIM_618_786"
    return None



def _requirement_met(
    requirement: str | None,
    snapshot: FeatureSnapshot,
    side: str,
    vwap_tolerance: Decimal = Decimal(0),
) -> bool:
    if requirement is None:
        return True
    if snapshot.last_close is None or snapshot.vwap is None:
        return False
    # Long: close back at or above VWAP. Short: close back at or below VWAP (mirror).
    if side == "long":
        return snapshot.last_close >= snapshot.vwap - vwap_tolerance
    return snapshot.last_close <= snapshot.vwap + vwap_tolerance


def _volume_climax(
    snapshot: FeatureSnapshot,
    previous: FeatureSnapshot | None,
    side: str,
) -> bool:
    """Upgrade 1 exit: one bar with RVOL >= 2.20 that closes against the position across VWAP."""
    if previous is None or snapshot.rvol is None or snapshot.rvol < CLIMAX_RVOL_MIN:
        return False
    if snapshot.vwap is None or snapshot.last_close is None or previous.last_close is None:
        return False
    if side == "long":
        return snapshot.last_close < snapshot.vwap and snapshot.last_close < previous.last_close
    return snapshot.last_close > snapshot.vwap and snapshot.last_close > previous.last_close


def _pullback_depth(snapshot: FeatureSnapshot, side: str) -> Decimal | None:
    return snapshot.pullback_depth if side == "long" else snapshot.pullback_depth_short


def _structure_target(
    snapshot: FeatureSnapshot,
    side: str,
    regime: Regime | None = None,
) -> tuple[Decimal | None, str | None]:
    if side == "long":
        if regime is Regime.TREND_DAY:
            target = snapshot.structure_target_continuation
            source = snapshot.structure_source_continuation
            if target is not None:
                return target, source
            if snapshot.structure_source == "SESSION_HIGH":
                return None, None
        return snapshot.structure_target, snapshot.structure_source
    return snapshot.structure_target_short, snapshot.structure_source_short


@dataclass(frozen=True, slots=True)
class ProjectedLevels:
    target: Decimal
    stop: Decimal
    net_move_room: Decimal
    reward_to_risk: Decimal
    target_source: str


def projected_levels(snapshot: FeatureSnapshot, open_space_mult: Decimal, max_mult: Decimal,
                     stop_mult: Decimal, roundtrip_cost: Decimal, slippage_buffer: Decimal,
                     side: str = "long", regime: Regime | None = None) -> ProjectedLevels | None:
    """Structural-primary target for an entry at last_close, mirrored for shorts.

    Long:  structure above entry (nearest), capped at entry + max*ATR; open space -> entry + open*ATR.
    Short: structure below entry (nearest), capped at entry - max*ATR; open space -> entry - open*ATR.
    Net MoveRoom = |target - entry| / entry - roundtrip cost - slippage buffer.
    Reward-to-risk = |target - entry| / (stop_mult*ATR).
    """
    if snapshot.atr is None or snapshot.last_close is None or snapshot.last_close <= 0 or snapshot.atr <= 0:
        return None
    entry = snapshot.last_close
    atr = snapshot.atr
    structure, src = _structure_target(snapshot, side, regime)
    sign = Decimal(1) if side == "long" else Decimal(-1)
    if structure is None:
        target = entry + sign * open_space_mult * atr
        source = "ATR_OPEN_SPACE"
    else:
        cap = entry + sign * max_mult * atr
        target = min(structure, cap) if side == "long" else max(structure, cap)
        source = src or "STRUCTURE"
        if target != structure:
            source = "ATR_EXTENDED_TO_" + str(max_mult) + "x"
    stop = entry - sign * stop_mult * atr
    gross = abs(target - entry) / entry
    rr = abs(target - entry) / (stop_mult * atr)
    return ProjectedLevels(target, stop, gross - roundtrip_cost - slippage_buffer, rr, source)


def _trend_broken(snapshot: FeatureSnapshot, pos_side: str, regime: Regime) -> bool:
    if pos_side == "long":
        trend_broken = snapshot.trend == "down" or snapshot.structure == "lower-highs-lower-lows"
    else:
        trend_broken = snapshot.trend == "up" or snapshot.structure == "higher-highs-higher-lows"
    if not trend_broken or regime is not Regime.TREND_DAY:
        return trend_broken
    if snapshot.last_close is None or snapshot.vwap is None:
        return False
    if pos_side == "long":
        return snapshot.last_close < snapshot.vwap
    return snapshot.last_close > snapshot.vwap


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
            raise TypeError("missing required strategy config")
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
        sides = self.strategy.get("sides", ["long", "short"])
        if not set(sides).issubset({"long", "short"}) or not sides:
            raise ValueError("sides must be a non-empty subset of long/short")
        self.sides = tuple(sides)
        self.policy = policy
        mr = self.strategy.get("move_room")
        if not isinstance(mr, Mapping):
            raise TypeError("missing move_room config")
        self.target_mult = Decimal(str(mr["target_atr_mult"]))
        self.max_target_mult = Decimal(str(mr.get("max_target_atr_mult", "4.0")))
        self.stop_mult = Decimal(str(mr["stop_atr_mult"]))
        self.min_rr = Decimal(str(mr["min_reward_to_risk"]))
        self.roundtrip_cost = Decimal(str(mr["roundtrip_cost_pct"]))
        self.slippage_buffer = Decimal(str(mr["slippage_buffer_pct"]))
        if self.stop_mult <= 0 or self.target_mult <= 0:
            raise ValueError("ATR multiples must be positive")
        if self.max_target_mult < self.target_mult:
            raise ValueError("max_target_atr_mult must be >= target_atr_mult")
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
        event_risk: EventRiskAssessment | None = None,
        known_roundtrip_cost_pct: Decimal | None = None,
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
        event_cfg = self.strategy.get("event_risk", {})
        event_required = bool(event_cfg.get("required", False))
        event_max_age = int(event_cfg.get("max_age_seconds", 900))
        event_fresh = event_risk is not None and event_risk.is_fresh_at(timestamp, event_max_age)
        if event_required and not event_fresh:
            gates.append("event_risk_unavailable_or_stale")
        elif event_required and event_risk is not None and event_risk.state != "clear":
            gates.append("event_risk_blocked")
        if regime.value not in self.strategy.get("allowed_regimes", []):
            gates.append("regime_not_allowed")

        market = None
        try:
            _market_name, market = self.policy.resolve_market(symbol)
        except ValueError:
            market = None

        engine = self.policy.config["engine"]
        flatten_mins = int(engine["mandatory_pre_close_flat_mins"])
        pre_close_flat = minutes_to_close is not None and minutes_to_close <= flatten_mins
        required_room = None
        slot_rvol = None
        roundtrip_cost_used = self.roundtrip_cost
        slippage_buffer_used = self.slippage_buffer
        if market is not None and "min_net_moveroom_pct" in market and "slot_rvol_min" in market:
            required_room = Decimal(str(market["min_net_moveroom_pct"]))
            slot_rvol = Decimal(str(market["slot_rvol_min"]))
            # Market-specific estimate feeds the pre-trade MoveRoom gate; realised costs
            # are calculated separately from config/costs.yaml and labelled by coverage.
            roundtrip_cost_used = max(
                self.roundtrip_cost,
                Decimal(str(market.get("est_roundtrip_cost_pct", self.roundtrip_cost))),
                known_roundtrip_cost_pct or Decimal(0),
            )
            slippage_buffer_used = Decimal(str(market.get("slippage_buffer_pct", self.slippage_buffer)))

        zone: str | None = None
        size_weight: Decimal | None = None
        side_used: str | None = None
        levels: ProjectedLevels | None = None
        action = "hold"
        last = snapshot.last_close
        # Upgrade 3: per-market archetype multipliers override the global ones when configured.
        stop_mult_used = self.stop_mult
        target_mult_used = self.target_mult
        max_mult_used = self.max_target_mult

        if position_quantity != 0:
            is_long = position_quantity > 0
            side_used = "long" if is_long else "short"
            if pre_close_flat:
                action = "exit_long" if is_long else "exit_short"
                zone = "MANDATORY_PRE_CLOSE_FLAT"
            elif position_stop is not None and last is not None and (
                last <= position_stop if is_long else last >= position_stop):
                action = "exit_long" if is_long else "exit_short"
                zone = "HARD_STOP"
            elif position_target is not None and last is not None and (
                last >= position_target if is_long else last <= position_target):
                action = "exit_long" if is_long else "exit_short"
                zone = "TARGET_HIT"
            elif _volume_climax(snapshot, previous_snapshot, side_used):
                action = "exit_long" if is_long else "exit_short"
                zone = "VOLUME_CLIMAX_EXIT"
            elif (_trend_broken(snapshot, side_used, regime) and previous_snapshot is not None
                  and _trend_broken(previous_snapshot, side_used, regime)):
                action = "exit_long" if is_long else "exit_short"
                zone = "TRENDLINE_BREAK" if (
                    snapshot.structure in {"lower-highs-lower-lows", "higher-highs-higher-lows"}
                ) else "CONFIRMED_BREAK"
        else:
            if snapshot.trend == "up" and "long" in self.sides:
                side_used = "long"
            elif snapshot.trend == "down" and "short" in self.sides:
                side_used = "short"
        if position_quantity == 0 and side_used is not None and policy.allowed and market is not None:
            timeframe_cfg = self.strategy.get("timeframes", {})
            if timeframe_cfg.get("require_completed_context", False):
                context_time = snapshot.context_timestamp
                if (snapshot.context_regime is None or context_time is None
                        or context_time > timestamp
                        or timestamp - context_time > timedelta(minutes=int(timeframe_cfg.get("max_context_age_mins", 30)))
                        or snapshot.context_regime != regime.value):
                    gates.append("30m_context_unavailable_or_stale")
                swing_cfg = self.strategy.get("swing", {})
                required_swing = Decimal(str(swing_cfg.get("min_atr_mult", "0.60")))
                expected_direction = "up" if side_used == "long" else "down"
                if (not snapshot.swing_15m_confirmed
                        or snapshot.swing_15m_direction != expected_direction
                        or snapshot.swing_15m_size_atr is None
                        or snapshot.swing_15m_size_atr < required_swing):
                    gates.append("15m_swing_unconfirmed_or_misaligned")
            trend_day_long = side_used == "long" and regime is Regime.TREND_DAY
            trend_day_short = side_used == "short" and regime is Regime.TREND_DAY
            trend_day_directional = trend_day_long or trend_day_short
            vwap_aligned = False
            if trend_day_long and last is not None and snapshot.vwap is not None and snapshot.atr is not None:
                vwap_aligned = last >= snapshot.vwap - Decimal("0.25") * snapshot.atr
            elif trend_day_short and last is not None and snapshot.vwap is not None and snapshot.atr is not None:
                vwap_aligned = last <= snapshot.vwap + Decimal("0.25") * snapshot.atr
            elif last is not None and snapshot.vwap is not None:
                vwap_aligned = last > snapshot.vwap if side_used == "long" else last < snapshot.vwap
            if snapshot.vwap is None or last is None or (trend_day_directional and snapshot.atr is None):
                gates.append("vwap_alignment_unavailable")
            elif not vwap_aligned:
                gates.append("vwap_alignment_not_met")
            directional_close_confirmed = (
                not trend_day_directional
                or (
                    previous_snapshot is not None
                    and last is not None
                    and previous_snapshot.last_close is not None
                    and (last > previous_snapshot.last_close if side_used == "long"
                         else last < previous_snapshot.last_close)
                )
            )
            if not directional_close_confirmed:
                gates.append("directional_close_confirmation_not_met")
            if required_room is None or slot_rvol is None:
                gates.append("market_thresholds_missing")
            else:
                # The pullback bar must always satisfy the market's baseline RVOL floor.
                # A prior impulse is an additional qualification, never a bypass of that floor.
                if snapshot.rvol is None:
                    gates.append("rvol_unavailable")
                else:
                    if snapshot.rvol < slot_rvol:
                        gates.append("rvol_below_threshold")
                    if snapshot.impulse_rvol is not None:
                        if snapshot.impulse_rvol < IMPULSE_RVOL_MIN:
                            gates.append("impulse_volume_not_met")
                        if snapshot.rvol > DRY_UP_RVOL_MAX:
                            gates.append("dry_up_not_met")
            if market is not None and "stop_mult" in market:
                stop_mult_used = Decimal(str(market["stop_mult"]))
            if market is not None and "target_mult" in market:
                target_mult_used = Decimal(str(market["target_mult"]))
                max_mult_used = max(self.max_target_mult, target_mult_used)
            levels = projected_levels(
                snapshot, target_mult_used, max_mult_used, stop_mult_used,
                roundtrip_cost_used, slippage_buffer_used, side_used, regime,
            )
            sufficient_room = False
            if levels is None:
                gates.append("move_room_unavailable")
            elif required_room is not None:
                sufficient_room = levels.net_move_room >= required_room
                if not sufficient_room:
                    gates.append("insufficient_move_room")
                if levels.reward_to_risk < self.min_rr:
                    sufficient_room = False
                    gates.append("insufficient_reward_to_risk")
            depth = _pullback_depth(snapshot, side_used)
            is_crypto = _market_name == "CRYPTO_MAJORS"
            zone = classify_reaction_zone(
                depth,
                regime,
                snapshot.impulse_rvol,
                snapshot.session_efficiency,
                allow_momentum_zone=not is_crypto,
            )
            if zone is None:
                gates.append("outside_reaction_zone")
            else:
                playbook = self.strategy.get("playbooks", {}).get(regime.value, {})
                allowed_zones = playbook.get("allowed_reaction_zones")
                if allowed_zones is not None and zone not in allowed_zones:
                    gates.append("regime_playbook_zone_not_allowed")
                    zone = None
            if zone is not None:
                _lo, _hi, size_weight, requirement = REACTION_ZONES[zone]
                vwap_tolerance = (
                    Decimal("0.25") * snapshot.atr
                    if trend_day_directional and snapshot.atr is not None
                    else Decimal(0)
                )
                if requirement == "momentum_vwap_hold":
                    # Momentum zone: hold the VWAP side AND a 1-bar hook in the trade direction.
                    prev_close = previous_snapshot.last_close if previous_snapshot is not None else None
                    hook = (
                        prev_close is not None and last is not None
                        and (last > prev_close if side_used == "long" else last < prev_close)
                    )
                    held = _requirement_met("vwap_hold", snapshot, side_used, vwap_tolerance)
                    if not (held and hook):
                        gates.append("momentum_vwap_hold_not_met")
                        zone = None
                elif not _requirement_met(requirement, snapshot, side_used, vwap_tolerance):
                    gates.append(f"{requirement}_not_met")
                    zone = None
            if (zone is not None and sufficient_room and not any(g in gates for g in VOLUME_GATES)
                    and "30m_context_unavailable_or_stale" not in gates
                    and "15m_swing_unconfirmed_or_misaligned" not in gates
                    and "event_risk_unavailable_or_stale" not in gates
                    and "event_risk_blocked" not in gates
                    and vwap_aligned and directional_close_confirmed):
                action = "enter_long" if side_used == "long" else "enter_short"

        inputs = {
            "trend": snapshot.trend,
            "structure": snapshot.structure,
            "rvol": str(snapshot.rvol),
            "slot_rvol": str(snapshot.rvol),
            "rolling_rvol": str(snapshot.rolling_rvol),
            "slot_rvol_samples": snapshot.slot_rvol_samples,
            "roundtrip_cost_estimate_pct": str(roundtrip_cost_used),
            "known_roundtrip_cost_pct": str(known_roundtrip_cost_pct) if known_roundtrip_cost_pct is not None else "N/A",
            "slippage_buffer_pct": str(slippage_buffer_used),
            "atr": str(snapshot.atr),
            "vwap": str(snapshot.vwap),
            "last_close": str(snapshot.last_close),
            "pullback_depth": str(snapshot.pullback_depth),
            "pullback_depth_short": str(snapshot.pullback_depth_short),
            "fib_382": str(snapshot.fib_382),
            "fib_618": str(snapshot.fib_618),
            "move_room": str(snapshot.move_room),
            "regime": regime.value,
            "stale_feed_seconds": stale_feed_seconds,
            "context_regime": snapshot.context_regime,
            "context_timestamp": snapshot.context_timestamp.isoformat() if snapshot.context_timestamp else None,
            "swing_15m_direction": snapshot.swing_15m_direction,
            "swing_15m_size_atr": str(snapshot.swing_15m_size_atr),
            "swing_15m_confirmed": snapshot.swing_15m_confirmed,
            "event_risk_state": event_risk.state if event_risk is not None else "unavailable",
            "event_risk_as_of": event_risk.as_of.isoformat() if event_risk is not None else None,
            "event_risk_reason": event_risk.reason if event_risk is not None else "no assessment supplied",
        }
        parameters = {
            "side": side_used or "none",
            "trend": snapshot.trend,
            "rvol": _decimal_text(snapshot.rvol),
            "slot_rvol": _decimal_text(snapshot.rvol),
            "rolling_rvol": _decimal_text(snapshot.rolling_rvol),
            "slot_rvol_samples": str(snapshot.slot_rvol_samples),
            "rvol_threshold": _decimal_text(slot_rvol),
            "pullback_depth": _decimal_text(_pullback_depth(snapshot, side_used) if side_used else None),
            "zone": zone or "N/A",
            "size_weight": _decimal_text(size_weight),
            "stop_mult": _decimal_text(stop_mult_used),
            "target_mult": _decimal_text(target_mult_used),
            "impulse_rvol": _decimal_text(snapshot.impulse_rvol),
            "target": _decimal_text(levels.target if levels else None),
            "target_source": levels.target_source if levels else "N/A",
            "net_move_room": _decimal_text(levels.net_move_room if levels else None),
            "move_room": _decimal_text(levels.net_move_room if levels else None),
            "move_room_threshold": _decimal_text(required_room),
            "roundtrip_cost_estimate_pct": _decimal_text(roundtrip_cost_used),
            "known_roundtrip_cost_pct": _decimal_text(known_roundtrip_cost_pct),
            "slippage_buffer_pct": _decimal_text(slippage_buffer_used),
            "reward_to_risk": _decimal_text(levels.reward_to_risk if levels else None),
            "max_risk_pct": "N/A" if policy.max_risk_pct is None else str(policy.max_risk_pct),
            "stale_seconds": _decimal_text(Decimal(str(stale_feed_seconds))),
            "stale_limit": _decimal_text(Decimal(str(stale_threshold_seconds))),
            "context_regime": snapshot.context_regime or "N/A",
            "swing_15m_direction": snapshot.swing_15m_direction or "N/A",
            "swing_15m_size_atr": _decimal_text(snapshot.swing_15m_size_atr),
            "swing_15m_confirmed": str(snapshot.swing_15m_confirmed),
            "event_risk_state": event_risk.state if event_risk is not None else "unavailable",
        }
        display = {
            "enter_long": "BUY", "enter_short": "SHORT",
            "exit_long": "SELL", "exit_short": "COVER",
        }.get(action, "HOLD")
        if action.startswith("enter_"):
            reason = (
                f"{display} {symbol}: {side_used} entry, RVOL {_decimal_text(snapshot.rvol)} >= "
                f"{_decimal_text(slot_rvol)}, pullback {_decimal_text(_pullback_depth(snapshot, side_used))} "
                f"in {zone}, target {_decimal_text(levels.target)} ({levels.target_source}), "
                f"net MoveRoom {_decimal_text(levels.net_move_room)} >= {_decimal_text(required_room)}, "
                f"R:R {_decimal_text(levels.reward_to_risk)}"
            )
        elif action.startswith("exit_"):
            if zone == "MANDATORY_PRE_CLOSE_FLAT":
                detail = f"mandatory pre-close flat ({minutes_to_close} minutes to close)"
            elif zone == "HARD_STOP":
                detail = f"hard stop hit at {_decimal_text(last)}"
            elif zone == "TARGET_HIT":
                detail = f"target hit at {_decimal_text(last)}"
            else:
                detail = f"confirmed trend break ({snapshot.trend}, {snapshot.structure})"
            reason = f"{display} {symbol}: {detail} [{side_used} position]"
        else:
            if gates:
                reason = f"HOLD {symbol}: blocked by {', '.join(gates)}"
            elif snapshot.trend not in {"up", "down"}:
                reason = f"HOLD {symbol}: trend is {snapshot.trend}, no side selected"
            else:
                reason = f"HOLD {symbol}: no entry conditions met ({snapshot.trend} trend)"
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
            expected_move_room=levels.net_move_room if levels else None,
            config_version=self.policy.config_version,
            signal_id=digest,
            dedupe_key=dedupe,
            display_action=display,
            parameters=parameters,
            reason=reason,
        )
