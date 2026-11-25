"""Actor 02, 04, and 06 implementations for the demo runtime."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Protocol


class BrokerAdapter(Protocol):
    def buy(self, payload: dict[str, object], idempotency_key: str | None = None) -> tuple[int, dict[str, object]]: ...
    def sell(self, payload: dict[str, object], idempotency_key: str | None = None) -> tuple[int, dict[str, object]]: ...
    def close_all(self) -> tuple[int, dict[str, object]]: ...


class Actor02RegimeScanner:
    """Two-stage configured-symbol scanner (30m discovery, 5m evaluation)."""

    def __init__(self, symbols: Iterable[Mapping[str, object]], regime_provider: object | None = None,
                 slot_rvol_min: float = 1.05) -> None:
        self.symbols = tuple(dict(symbol) for symbol in symbols)
        self.regime_provider = regime_provider
        self.slot_rvol_min = slot_rvol_min

    def discover(self, market_open: bool = True, regime: str = "TREND_DAY") -> list[dict[str, object]]:
        if regime == "NEWS_DAY" or not market_open:
            return []
        candidates: list[dict[str, object]] = []
        for spec in self.symbols:
            if not spec.get("enabled", False) or spec.get("symbol") == "BTC-USD":
                continue
            slot_rvol = float(spec.get("slot_rvol", spec.get("slot_RVOL", self.slot_rvol_min)))
            if slot_rvol >= self.slot_rvol_min:
                candidates.append({"symbol": spec["symbol"], "stage": "discovery_30m",
                                   "regime": regime, "slot_RVOL": slot_rvol,
                                   "narrative": "Configured symbol passed 30-minute discovery gates"})
        return candidates

    def evaluate(self, candidates: Iterable[Mapping[str, object]], reaction: Mapping[str, bool] | None = None) -> list[dict[str, object]]:
        reaction = reaction or {}
        cards: list[dict[str, object]] = []
        for candidate in candidates:
            symbol = str(candidate["symbol"])
            allowed = reaction.get(symbol, False)
            cards.append({"symbol": symbol, "stage": "evaluation_5m", "display_action": "BUY" if allowed else "HOLD",
                          "reason": "Confluence_v1 reaction-zone confluence detected" if allowed else "No 5-minute reaction-zone confluence",
                          "slot_RVOL": candidate.get("slot_RVOL")})
        return cards


class Actor04ExecutionEngine:
    """Consumes explainable BUY/SELL cards and routes only to a mock/demo adapter."""

    def __init__(self, adapter: BrokerAdapter) -> None:
        self.adapter = adapter

    def execute(self, card: Mapping[str, object], quantity: object = 1,
                idempotency_key: str | None = None) -> tuple[int, dict[str, object]]:
        action = str(card.get("display_action", "HOLD")).upper()
        if action not in {"BUY", "SELL"}:
            return 200, {"status": "held", "reason": str(card.get("reason", "No action"))}
        payload = {"symbol": card["symbol"], "quantity": quantity,
                   "signal_id": card.get("signal_id", card.get("card_id", "actor04")),
                   "narrative": str(card.get("reason", f"Actor 04 {action} decision card"))}
        if action == "BUY":
            return self.adapter.buy(payload, idempotency_key)
        return self.adapter.sell(payload, idempotency_key)

    def emergency_close_all(self) -> tuple[int, dict[str, object]]:
        return self.adapter.close_all()


class Actor06TradeAnalyzer:
    """Post-trade metrics and probation/disable rectification."""

    def analyze(self, trades: Iterable[Mapping[str, object]]) -> dict[str, object]:
        items = list(trades)
        pnl: list[Decimal] = []
        for trade in items:
            if trade.get("side") == "sell" and "realized_pnl" in trade:
                pnl.append(Decimal(str(trade["realized_pnl"])))
        gross_profit = sum((value for value in pnl if value > 0), Decimal(0))
        gross_loss = abs(sum((value for value in pnl if value < 0), Decimal(0)))
        profit_factor = gross_profit / gross_loss if gross_loss else (Decimal("Infinity") if gross_profit else Decimal(0))

        peak = Decimal(0)
        equity = Decimal(0)
        max_drawdown = Decimal(0)
        streak = 0
        max_loss_streak = 0
        for value in pnl:
            equity += value
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)
            streak = streak + 1 if value < 0 else 0
            max_loss_streak = max(max_loss_streak, streak)

        wins = sum(value > 0 for value in pnl)
        win_rate = (Decimal(wins) / Decimal(len(pnl))) if pnl else Decimal(0)
        return {"trade_count": len(pnl), "win_rate": str(win_rate), "profit_factor": str(profit_factor),
                "max_drawdown": str(max_drawdown), "consecutive_loss_streak": max_loss_streak,
                "rectifications": self.rectify(profit_factor)}

    def rectify(self, profit_factor: Decimal) -> dict[str, str]:
        if profit_factor == Decimal("Infinity"):
            pf = Decimal(999999)
        else:
            pf = profit_factor
        nvda = "probation" if pf <= Decimal("1.25") else "allowed"
        hkex = "disabled" if pf <= Decimal("1.10") else "allowed"
        return {"NVDA": nvda, "HKEX": hkex, "SLV": "excluded"}


# Backward-compatible descriptive alias
Actor06TradeAnalyzerAndRectifier = Actor06TradeAnalyzer
