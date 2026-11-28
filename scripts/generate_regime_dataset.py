"""Generate deterministic synthetic regime datasets on exchange-local sessions.

Output: one zip with layout  ten-day-regimes/<regime>/<interval>/market_bars_<interval>.csv
  Each file is a single combined CSV: symbol,timestamp,open,high,low,close,volume
  intervals: 1m, 5m, 15m, 30m    regimes: bull-week, normal-week, side-week, great-choppy-week
  10 sessions per regime. Timestamps are UTC bar starts; bars are aligned to each market's open
  (crypto is 24x7 UTC). Bars are built only from full session buckets.
Synthetic data: label outputs as synthetic; it supports software checks, not performance claims.
"""

import os
import zipfile
from datetime import time

import numpy as np
import pandas as pd

SEED = 20261128
OUT_DIR = "ten_day_regime_ticks"
ZIP_NAME = "ten-day-regimes-1m-5m-15m-30m.zip"

# Session tables match app/backtest/dataset_runner.MARKET_SESSIONS (exchange-local times).
SESSIONS = {
    "US_EQUITIES": ("America/New_York", time(9, 30), time(16, 0)),
    "US_COMMODITIES": ("America/New_York", time(9, 30), time(16, 0)),
    "CRYPTO_MAJORS": ("UTC", None, None),
    "INDIA_NSE": ("Asia/Kolkata", time(9, 15), time(15, 30)),
    "CANADA_TSX": ("America/Toronto", time(9, 30), time(16, 0)),
    "UK_LSE": ("Europe/London", time(8, 0), time(16, 30)),
    "GERMANY_XETRA": ("Europe/Berlin", time(9, 0), time(17, 30)),
    "NETHERLANDS_AEX": ("Europe/Amsterdam", time(9, 0), time(17, 30)),
    "JAPAN_TSE": ("Asia/Tokyo", time(9, 0), time(15, 0)),
    "SINGAPORE_SGX": ("Asia/Singapore", time(9, 0), time(17, 0)),
    "AUSTRALIA_ASX": ("Australia/Sydney", time(10, 0), time(16, 0)),
    "KOREA_KRX": ("Asia/Seoul", time(9, 0), time(15, 30)),
}

SYMBOLS = [
    # One deterministic fixture symbol for each supported backtest category.
    ("AAPL", "US_EQUITIES", 230.0, 0.00045),
    ("SPY", "US_EQUITIES", 585.0, 0.00035),
    ("SPX", "US_EQUITIES", 6700.0, 0.00025),
    ("GC=F", "US_COMMODITIES", 2650.0, 0.00040),
    ("CL=F", "US_COMMODITIES", 65.0, 0.00065),
    ("NVDA", "US_EQUITIES", 142.0, 0.00065),
    ("GLD", "US_COMMODITIES", 252.0, 0.00030),
    ("USO", "US_COMMODITIES", 76.0, 0.00055),
    ("BTC-USD", "CRYPTO_MAJORS", 88500.0, 0.00080),
    ("ETH-USD", "CRYPTO_MAJORS", 3150.0, 0.00090),
    ("NIFTYBEES.NS", "INDIA_NSE", 272.0, 0.00040),
    ("RELIANCE.NS", "INDIA_NSE", 1310.0, 0.00050),
    ("XIU.TO", "CANADA_TSX", 39.5, 0.00030),
    ("ISF.L", "UK_LSE", 825.0, 0.00030),
    ("SAP.DE", "GERMANY_XETRA", 224.0, 0.00045),
    ("ASML.AS", "NETHERLANDS_AEX", 695.0, 0.00058),
    ("1306.T", "JAPAN_TSE", 2780.0, 0.00035),
    ("D05.SI", "SINGAPORE_SGX", 41.2, 0.00035),
    ("BHP.AX", "AUSTRALIA_ASX", 41.8, 0.00045),
    ("005930.KS", "KOREA_KRX", 58500.0, 0.00055),
]

REGIMES = {
    "bull-week": {"start": "2026-10-19", "drift": +0.000018, "wave": 0.00012, "vol": 1.0},
    "normal-week": {"start": "2026-11-02", "drift": +0.000003, "wave": 0.00010, "vol": 1.0},
    "side-week": {"start": "2026-11-16", "drift": 0.0, "wave": 0.00006, "vol": 0.75},
    "great-choppy-week": {"start": "2026-11-30", "drift": -0.000002, "wave": 0.00025, "vol": 1.65},
}
INTERVALS = {"1m": 1, "5m": 5, "15m": 15, "30m": 30}
SESSIONS_PER_REGIME = 10


def session_minutes(profile: str, day: pd.Timestamp) -> pd.DatetimeIndex:
    """1-minute bar starts for one session day, returned in UTC."""
    tz, open_t, close_t = SESSIONS[profile]
    if open_t is None:  # 24x7 crypto
        start = pd.Timestamp(day.date(), tz="UTC")
        return pd.date_range(start, periods=1440, freq="1min")
    start = pd.Timestamp.combine(day.date(), open_t).tz_localize(tz)
    end = pd.Timestamp.combine(day.date(), close_t).tz_localize(tz)
    return pd.date_range(start, end, freq="1min", inclusive="left").tz_convert("UTC")


def build_1m(profile: str, days: pd.DatetimeIndex) -> pd.DataFrame:
    """Session-only 1m bars with a session-day key and in-session minute index."""
    rows = []
    for day in days:
        mins = session_minutes(profile, day)
        rows.append(pd.DataFrame({"ts": mins, "day": day.date(), "m": np.arange(len(mins))}))
    return pd.concat(rows, ignore_index=True)


def price_path(base_px: float, base_vol: float, cfg: dict, n: int, rng: np.random.Generator) -> pd.DataFrame:
    idx = np.arange(n)
    wave = cfg["wave"] * np.sin(2.0 * np.pi * idx / 360.0)
    rets = cfg["drift"] + wave + rng.normal(0.0, base_vol * cfg["vol"], size=n)
    closes = base_px * np.cumprod(1.0 + rets)
    opens = np.roll(closes, 1)
    opens[0] = base_px
    pad = np.abs(rng.normal(base_vol * cfg["vol"] * 0.6, base_vol * cfg["vol"] * 0.3, size=n))
    highs = np.maximum(opens, closes) * (1.0 + pad)
    lows = np.minimum(opens, closes) * (1.0 - pad)
    vols = rng.integers(250, 4500, size=n)
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes, "volume": vols})


def aggregate(frame: pd.DataFrame, k: int) -> pd.DataFrame:
    """Aggregate 1m bars into k-minute buckets within each session; keep only full buckets."""
    if k == 1:
        return frame
    key = frame["day"].astype(str) + "|" + (frame["m"] // k).astype(str)
    grouped = frame.groupby(key, sort=False)
    out = grouped.agg(ts=("ts", "first"), open=("open", "first"), high=("high", "max"),
                      low=("low", "min"), close=("close", "last"), volume=("volume", "sum"),
                      n=("ts", "size")).reset_index(drop=True)
    return out[out["n"] == k].drop(columns="n").reset_index(drop=True)


def main() -> None:
    rng = np.random.default_rng(SEED)
    os.makedirs(OUT_DIR, exist_ok=True)
    count = 0
    with zipfile.ZipFile(ZIP_NAME, "w", zipfile.ZIP_DEFLATED) as zf:
        for regime, cfg in REGIMES.items():
            days = pd.bdate_range(cfg["start"], periods=SESSIONS_PER_REGIME)
            per_interval: dict[str, list[pd.DataFrame]] = {tf: [] for tf in INTERVALS}
            for sym, profile, base_px, base_vol in SYMBOLS:
                frame = build_1m(profile, days)
                path = price_path(base_px, base_vol, cfg, len(frame), rng)
                frame = pd.concat([frame, path], axis=1)
                for tf, minutes in INTERVALS.items():
                    bars = aggregate(frame, minutes)
                    out = pd.DataFrame({
                        "symbol": sym,
                        "timestamp": pd.DatetimeIndex(bars["ts"]).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "open": np.round(bars["open"], 4),
                        "high": np.round(bars["high"], 4),
                        "low": np.round(bars["low"], 4),
                        "close": np.round(bars["close"], 4),
                        "volume": bars["volume"].astype(int),
                    })
                    per_interval[tf].append(out)
            # One combined file per regime and interval: every symbol, every bar, one header.
            for tf in INTERVALS:
                combined = pd.concat(per_interval[tf], ignore_index=True)
                local = os.path.join(OUT_DIR, f"{regime}_{tf}_market_bars.csv")
                combined.to_csv(local, index=False)
                zf.write(local, arcname=f"ten-day-regimes/{regime}/{tf}/market_bars_{tf}.csv")
                count += 1
    print(f"wrote {ZIP_NAME} with {count} combined files ({len(REGIMES)} regimes x {len(INTERVALS)} intervals, {len(SYMBOLS)} symbols each)")


if __name__ == "__main__":
    main()
