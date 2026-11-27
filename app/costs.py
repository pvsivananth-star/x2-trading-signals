"""Transaction cost loader. Fail-closed: a required cost that is not_found blocks the calculation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml


class CostBlockedError(RuntimeError):
    """Raised when a required cost value is missing or unverified for the requested market."""


def load_costs(path: str | Path = "config/costs.yaml") -> dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "allow_estimates" not in data:
        raise ValueError("costs.yaml must define allow_estimates")
    return data


def _field(costs: dict[str, Any], market: str, name: str) -> Decimal:
    requested = f"{market}.{name}"
    visited: set[str] = set()
    while market not in visited:
        visited.add(market)
        section = costs.get(market)
        if not isinstance(section, dict):
            raise CostBlockedError(f"{requested}: missing")
        if name in section:
            entry = section[name]
            if not isinstance(entry, dict):
                raise CostBlockedError(f"{requested}: invalid cost entry")
            status = entry.get("status")
            if status == "verified":
                return Decimal(str(entry["value"]))
            if status == "estimate" and costs.get("allow_estimates") is True:
                return Decimal(str(entry["value"]))
            raise CostBlockedError(f"{requested}: status={status}; blocked")
        parent = section.get("inherits")
        if not isinstance(parent, str) or not parent:
            raise CostBlockedError(f"{requested}: missing")
        market = parent
    raise CostBlockedError(f"{requested}: inheritance cycle")


@dataclass(frozen=True, slots=True)
class RoundTripCost:
    market: str
    notional: Decimal
    commission: Decimal
    regulatory: Decimal
    spread: Decimal
    slippage: Decimal

    @property
    def total(self) -> Decimal:
        return self.commission + self.regulatory + self.spread + self.slippage

    @property
    def total_pct(self) -> Decimal:
        return self.total / self.notional


def us_equity_roundtrip(costs: dict[str, Any], price: Decimal, shares: Decimal,
                        market: str = "US_EQUITIES") -> RoundTripCost:
    """Round trip (buy then sell) for US listed shares/ETFs on IBKR Pro Tiered."""
    notional = price * shares
    rate = _field(costs, market, "commission_per_share")
    min_order = _field(costs, market, "commission_min_per_order")
    cap = _field(costs, market, "commission_max_pct_of_trade")
    per_side = max(min_order, rate * shares)
    per_side = min(per_side, cap * notional)
    commission = per_side * 2
    sec = _field(costs, market, "sec_fee_sell_rate") * notional
    taf = _field(costs, market, "finra_taf_sell_per_share") * shares
    cat = _field(costs, market, "finra_cat_per_share") * shares * 2
    clearing = _field(costs, market, "clearing_nscc_dtc_per_share") * shares * 2
    exchange = _field(costs, market, "exchange_fee_per_share") * shares * 2
    regulatory = sec + taf + cat + clearing + exchange
    spread = _field(costs, market, "spread_pct_full") * notional
    slippage = _field(costs, market, "slippage_pct_per_side") * notional * 2
    return RoundTripCost(market, notional, commission, regulatory, spread, slippage)


def required_gross_move(min_net_room: Decimal, round_trip_pct: Decimal) -> Decimal:
    """Gross move (fraction of entry) needed so net room clears min_net_room after costs."""
    return min_net_room + round_trip_pct
