"""Strict runtime-mode isolation for offline, demo, backtest, and production."""
from __future__ import annotations

import os

ALLOWED_MODES = {"backtest", "demo", "offline", "prod"}


def runtime_mode() -> str:
    mode = os.getenv("TRADING_MODE", "demo").strip().lower()
    if mode not in ALLOWED_MODES:
        raise RuntimeError("TRADING_MODE must be backtest, demo, offline, or prod")
    if mode == "prod" and os.getenv("X2_LIVE_BROKER_PHASE") != "2":
        raise RuntimeError("prod live execution requires broker phase 2")
    if mode == "backtest" and (os.getenv("X2_LIVE_BROKER_URL") or os.getenv("LIVE_BROKER_URL")):
        raise RuntimeError("backtest mode forbids live broker configuration")
    return mode


def backtest_isolated() -> bool:
    return runtime_mode() == "backtest"


def live_feed_allowed() -> bool:
    return runtime_mode() == "prod"
