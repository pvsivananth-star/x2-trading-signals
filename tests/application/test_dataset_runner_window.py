from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.backtest.bars import Bar
from app.backtest.dataset_runner import causal_regime
from app.features import DEFAULT_SESSION_CLOSE, DEFAULT_SESSION_OPEN, DEFAULT_SESSION_TZ, _local_dt, feature_snapshot


def _bars() -> list[Bar]:
    result: list[Bar] = []
    for day in (20, 23, 24):
        start = datetime(2026, 11, day, 14, 30, tzinfo=UTC)
        for index in range(12):
            price = Decimal("100") + Decimal(day - 20) + Decimal(index) / Decimal("10")
            result.append(
                Bar(
                    start + timedelta(minutes=5 * index),
                    start + timedelta(minutes=5 * (index + 1)),
                    price,
                    price + Decimal("0.5"),
                    price - Decimal("0.4"),
                    price + Decimal("0.1"),
                    Decimal(1000 + index * 10),
                    10,
                )
            )
    return result


class DatasetRunnerWindowTests(unittest.TestCase):
    def test_bounded_two_session_window_matches_full_causal_history(self) -> None:
        bars = _bars()
        window: list[Bar] = []
        window_session = None
        session = {
            "session_timezone": DEFAULT_SESSION_TZ,
            "session_open": DEFAULT_SESSION_OPEN,
            "session_close": DEFAULT_SESSION_CLOSE,
        }

        for index, bar in enumerate(bars):
            # The fixture timestamps are UTC; the strategy session date is New York local.
            current_date = _local_dt(bar.bar_end, DEFAULT_SESSION_TZ).date()
            if window_session != current_date:
                if window_session is not None:
                    window = [
                        item for item in window
                        if _local_dt(item.bar_end, DEFAULT_SESSION_TZ).date() == window_session
                    ]
                window_session = current_date
            window.append(bar)

            full_snapshot = feature_snapshot(bars[: index + 1], bar.bar_end, **session)
            bounded_snapshot = feature_snapshot(window, bar.bar_end, **session)
            self.assertEqual(full_snapshot, bounded_snapshot)
            self.assertEqual(
                causal_regime(bars[: index + 1], bar.bar_end, **session),
                causal_regime(window, bar.bar_end, **session),
            )
