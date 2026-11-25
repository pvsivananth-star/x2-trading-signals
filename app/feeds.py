"""Configurable, provider-neutral public market candle feeds."""

from __future__ import annotations

import csv
import json
import logging
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True, slots=True)
class Candle:
    bar_start: datetime
    bar_end: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trade_count: int = 0
    source: str = "unknown"
    synthetic: bool = False


@dataclass(frozen=True, slots=True)
class FeedSpec:
    symbol: str
    market: str
    category: str
    provider: str
    refresh_seconds: int = 60
    timeframes: tuple[str, ...] = ("1m", "5m", "15m", "30m", "1d")
    enabled: bool = True
    license_status: str = "unknown"


@dataclass(frozen=True, slots=True)
class FeedConfig:
    symbols: tuple[FeedSpec, ...]
    refresh_seconds: int
    stale_threshold_seconds: int
    fallback_fixture: Path
    providers: Mapping[str, Mapping[str, str]]


def load_feed_config(path: str | Path) -> FeedConfig:
    import yaml

    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8") or "{}")
    if not isinstance(raw, Mapping) or not isinstance(raw.get("feeds"), Mapping):
        raise TypeError("feed config must contain feeds")
    feeds = raw["feeds"]
    specs: list[FeedSpec] = []
    allowed_tf = {"1m", "5m", "15m", "30m", "1d"}
    for item in feeds.get("symbols", []):
        if not isinstance(item, Mapping):
            raise TypeError("feed symbol entry must be a mapping")
        symbol = str(item["symbol"])
        tfs = tuple(str(v) for v in item.get("timeframes", []))
        if not tfs or not set(tfs).issubset(allowed_tf):
            raise ValueError(f"invalid timeframes for {symbol}")
        specs.append(FeedSpec(symbol, str(item["market"]), str(item["category"]),
                              str(item["provider"]), int(item.get("refresh_seconds", feeds.get("refresh_seconds", 60))),
                              tfs, item.get("enabled") is True, str(item.get("license_status", "unknown"))))
    providers = feeds.get("providers", {})
    if not isinstance(providers, Mapping):
        raise TypeError("providers must be a mapping")
    fixture = Path(feeds.get("fallback_fixture", "data/fixtures/multi_asset/5-day-2026-11-20-to-2026-11-24"))
    return FeedConfig(tuple(specs), int(feeds.get("refresh_seconds", 60)),
                      int(feeds.get("stale_threshold_seconds", 300)), fixture,
                      {str(k): dict(v) for k, v in providers.items() if isinstance(v, Mapping)})


class CandleQuality:
    @staticmethod
    def validate(candles: Iterable[Candle]) -> list[Candle]:
        result: list[Candle] = []
        previous: datetime | None = None
        for candle in candles:
            if candle.bar_end <= candle.bar_start:
                continue
            if candle.open <= 0 or candle.high <= 0 or candle.low <= 0 or candle.close <= 0:
                continue
            if candle.low > min(candle.open, candle.close) or candle.high < max(candle.open, candle.close):
                continue
            if candle.volume < 0:
                continue
            if previous is not None and candle.bar_start < previous:
                continue
            result.append(candle)
            previous = candle.bar_end
        return result


class ProviderAdapter:
    name = ""
    def fetch(self, spec: FeedSpec, interval: str, limit: int = 100) -> list[Candle]:
        raise NotImplementedError


class YahooChartAdapter(ProviderAdapter):
    name = "yahoo_chart"

    def __init__(self, base_url: str = "https://query1.finance.yahoo.com", timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def fetch(self, spec: FeedSpec, interval: str, limit: int = 100) -> list[Candle]:
        period = {"1m": "1d", "5m": "5d", "15m": "1mo", "30m": "1mo", "1d": "1y"}[interval]
        url = f"{self.base_url}/v8/finance/chart/{quote(spec.symbol, safe='')}?{urlencode({'interval': interval, 'range': period})}"
        return _fetch_json_candles(url, self.timeout, "yahoo_chart", spec.symbol, interval)


class StooqAdapter(ProviderAdapter):
    name = "stooq"

    def __init__(self, base_url: str = "https://stooq.com", timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def fetch(self, spec: FeedSpec, interval: str, limit: int = 100) -> list[Candle]:
        stooq_interval = {"1d": "d", "1m": "1", "5m": "5", "15m": "15", "30m": "30"}[interval]
        symbol = spec.symbol.lower().replace("=", "")
        url = f"{self.base_url}/q/d/l/?{urlencode({'s': symbol, 'i': stooq_interval, 'd1': '20200101'})}"
        req = Request(url, headers={"User-Agent": "x2-trading-signals/1.0"})
        with urlopen(req, timeout=self.timeout) as response:
            text = response.read().decode("utf-8")
        rows = csv.DictReader(text.splitlines())
        candles: list[Candle] = []
        for row in rows:
            try:
                if interval == "1d":
                    start = datetime.strptime(row["Date"], "%Y-%m-%d").replace(tzinfo=UTC)
                else:
                    start = datetime.fromisoformat(row["Date"].replace(" ", "T")).replace(tzinfo=UTC)
                o, h, l, c = (Decimal(row[name]) for name in ("Open", "High", "Low", "Close"))
                duration = 86400 if interval == "1d" else int(interval[:-1]) * 60
                end = start if interval == "1d" else start.replace(microsecond=0)
                if interval != "1d":
                    end = datetime.fromtimestamp(start.timestamp() + duration, tz=UTC)
                else:
                    end = datetime.fromtimestamp(start.timestamp() + duration, tz=UTC)
                candles.append(Candle(start, end, o, h, l, c, Decimal(row.get("Volume") or 0), source="stooq"))
            except (KeyError, ValueError, InvalidOperation):
                continue
        return CandleQuality.validate(candles)[-limit:]


class BinanceKlinesAdapter(ProviderAdapter):
    name = "binance"

    def __init__(self, base_url: str = "https://api.binance.com", timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def fetch(self, spec: FeedSpec, interval: str, limit: int = 100) -> list[Candle]:
        venue = spec.symbol.replace("-USD", "USDT")
        query = urlencode({"symbol": venue, "interval": interval, "limit": min(limit, 1000)})
        url = f"{self.base_url}/api/v3/klines?{query}"
        req = Request(url, headers={"User-Agent": "x2-trading-signals/1.0"})
        with urlopen(req, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        candles: list[Candle] = []
        for row in payload:
            start_ms, end_ms = int(row[0]), int(row[6])
            start = datetime.fromtimestamp(start_ms / 1000, tz=UTC)
            end = datetime.fromtimestamp(end_ms / 1000, tz=UTC)
            candles.append(Candle(start, end, Decimal(str(row[1])), Decimal(str(row[2])), Decimal(str(row[3])),
                                  Decimal(str(row[4])), Decimal(str(row[5])), int(row[8]), "binance"))
        return CandleQuality.validate(candles)[-limit:]


def _fetch_json_candles(url: str, timeout: float, source: str, symbol: str, interval: str = "1m") -> list[Candle]:
    req = Request(url, headers={"User-Agent": "x2-trading-signals/1.0"})
    with urlopen(req, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    chart = payload.get("chart", payload).get("result")
    if not chart:
        return []
    result = chart[0]
    timestamps = result.get("timestamp", [])
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    candles: list[Candle] = []
    for i, ts in enumerate(timestamps):
        try:
            opens, highs, lows, closes, volumes = (quote[k][i] for k in ("open", "high", "low", "close", "volume") )
            if None in (opens, highs, lows, closes):
                continue
            start = datetime.fromtimestamp(ts, tz=UTC)
            duration = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1d": 86400}[interval]
            end = datetime.fromtimestamp(ts + duration, tz=UTC)
            candles.append(Candle(start, end, Decimal(str(opens)), Decimal(str(highs)), Decimal(str(lows)),
                                  Decimal(str(closes)), Decimal(str(volumes or 0)), source=source))
        except (KeyError, IndexError, InvalidOperation, TypeError):
            continue
    return CandleQuality.validate(candles)


class FeedStore:
    def __init__(self, config: FeedConfig) -> None:
        self.config = config
        self._bars: dict[tuple[str, str], tuple[Candle, ...]] = {}
        self._last_fetch: dict[str, datetime] = {}
        self._last_error: dict[str, str] = {}
        self._lock = threading.RLock()
        self.load_fallback()

    def load_fallback(self) -> None:
        fixture = self.config.fallback_fixture
        manifest = fixture / "manifest.json"
        if not manifest.is_file():
            return
        for spec in self.config.symbols:
            fixture_symbol = "SPX" if spec.symbol == "^GSPC" else spec.symbol
            safe = fixture_symbol.replace("=", "_").replace("/", "_")
            for interval in spec.timeframes:
                source_interval = interval if interval in {"1m", "5m", "15m"} else "15m"
                path = fixture / "bars" / f"{safe}-{source_interval}.csv"
                if not path.is_file():
                    continue
                candles = self._read_fixture(path, interval)
                if candles:
                    with self._lock:
                        self._bars[(spec.symbol, interval)] = tuple(candles)
                        self._last_fetch.setdefault(spec.symbol, datetime.now(UTC))

    def _read_fixture(self, path: Path, interval: str) -> list[Candle]:
        candles: list[Candle] = []
        with path.open("r", encoding="utf-8", newline="") as source:
            for row in csv.DictReader(source):
                try:
                    start = datetime.fromisoformat(row["bar_start"])
                    end = datetime.fromisoformat(row["bar_end"])
                    candles.append(Candle(start, end, Decimal(row["open"]), Decimal(row["high"]),
                                          Decimal(row["low"]), Decimal(row["close"]), Decimal(row["volume"]),
                                          int(row["trade_count"]), "deterministic_synthetic_fixture", True))
                except (KeyError, ValueError, InvalidOperation):
                    continue
        return CandleQuality.validate(candles)

    def bars(self, symbol: str, interval: str = "1m", limit: int = 100) -> list[Candle]:
        with self._lock:
            return list(self._bars.get((symbol, interval), ()))[-max(1, min(limit, 1000)):]

    def market_symbols(self) -> list[dict[str, object]]:
        return [{"symbol": s.symbol, "market": s.market, "category": s.category,
                 "provider": s.provider, "enabled": s.enabled,
                 "license_status": s.license_status,
                 "timeframes": list(s.timeframes)} for s in self.config.symbols]

    def stale_seconds(self, symbol: str, now: datetime | None = None) -> float:
        stamp = self._last_fetch.get(symbol)
        if stamp is None:
            return float("inf")
        now = now or datetime.now(UTC)
        return max(0.0, (now.astimezone(UTC) - stamp.astimezone(UTC)).total_seconds())

    def put(self, symbol: str, interval: str, candles: Iterable[Candle]) -> None:
        validated = CandleQuality.validate(candles)
        if not validated:
            return
        with self._lock:
            self._bars[(symbol, interval)] = tuple(validated[-1000:])
            self._last_fetch[symbol] = datetime.now(UTC)


class FeedRuntime:
    def __init__(self, config: FeedConfig, store: FeedStore | None = None, timeout: float = 10.0) -> None:
        self.config = config
        self.store = store or FeedStore(config)
        self.timeout = timeout
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def refresh_once(self) -> dict[str, str]:
        statuses: dict[str, str] = {}
        for spec in self.config.symbols:
            if not spec.enabled:
                statuses[spec.symbol] = "disabled"
                continue
            provider_config = self.config.providers.get(spec.provider, {})
            try:
                base_url = str(provider_config.get("base_url", ""))
                if spec.provider == "yahoo_chart":
                    adapter: ProviderAdapter = YahooChartAdapter(base_url or "https://query1.finance.yahoo.com", self.timeout)
                elif spec.provider == "stooq":
                    adapter = StooqAdapter(base_url or "https://stooq.com", self.timeout)
                elif spec.provider == "binance":
                    adapter = BinanceKlinesAdapter(base_url or "https://api.binance.com", self.timeout)
                else:
                    statuses[spec.symbol] = "unknown_provider"
                    continue
                fetched = False
                for interval in spec.timeframes:
                    candles = adapter.fetch(spec, interval)
                    if candles:
                        normalized = tuple(c for c in candles if not c.synthetic)
                        if normalized:
                            self.store.put(spec.symbol, interval, normalized)
                            fetched = True
                statuses[spec.symbol] = "ok" if fetched else "fallback_synthetic"
            except (OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
                self.store._last_error[spec.symbol] = type(exc).__name__
                logging.getLogger("x2.feeds").warning("feed unavailable source=%s symbol=%s", spec.provider, spec.symbol)
                statuses[spec.symbol] = "fallback_synthetic"
        return statuses

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="x2-feeds", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop.wait(0.1):
            self.refresh_once()
            if self._stop.wait(self.config.refresh_seconds):
                break
