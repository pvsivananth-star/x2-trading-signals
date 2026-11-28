"""Preflight validation for deterministic backtest datasets.

Validation is intentionally independent from strategy logic. A dataset must pass critical
integrity checks before profitability results are considered valid.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.real_data import REQUIRED_COLUMNS, _parse_ts

CORPORATE_ACTION_MODES = {"raw", "split_adjusted", "corporate_action_adjusted", "unadjusted"}


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    severity: str
    message: str
    instrument_id: int | None = None
    interval_seconds: int | None = None
    row: int | None = None


@dataclass(frozen=True, slots=True)
class DatasetValidationReport:
    dataset: str
    bars_file: str
    instruments_file: str
    rows: int
    groups: int
    issues: tuple[ValidationIssue, ...]
    checked_intervals: tuple[int, ...]
    symbols: tuple[str, ...]
    synthetic: bool
    warmup_rows: int
    warmup_groups: int = 0

    @property
    def critical(self) -> tuple[ValidationIssue, ...]:
        return tuple(i for i in self.issues if i.severity == "critical")

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        return tuple(i for i in self.issues if i.severity == "warning")

    @property
    def valid(self) -> bool:
        return not self.critical

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "bars_file": self.bars_file,
            "instruments_file": self.instruments_file,
            "rows": self.rows,
            "groups": self.groups,
            "checked_intervals": list(self.checked_intervals),
            "symbols": list(self.symbols),
            "synthetic": self.synthetic,
            "warmup_rows": self.warmup_rows,
            "warmup_groups": self.warmup_groups,
            "valid": self.valid,
            "critical_count": len(self.critical),
            "warning_count": len(self.warnings),
            "issues": [asdict(i) for i in self.issues],
        }


class DatasetValidationError(ValueError):
    """Raised when a dataset contains critical integrity violations."""


def _decimal(value: str, field: str, row: int, issues: list[ValidationIssue]) -> Decimal | None:
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        issues.append(ValidationIssue("INVALID_NUMBER", "critical", f"{field} is not numeric", row=row))
        return None


def validate_dataset(
    directory: str | Path,
    *,
    interval_seconds: int | None = None,
    profile_map: dict[str, str] | None = None,
    fail_on_warnings: bool = False,
    require_profile: bool = False,
) -> DatasetValidationReport:
    """Validate bars.csv/instruments.csv without executing a strategy."""
    directory = Path(directory).resolve()
    bars_path = directory / "bars.csv"
    instruments_path = directory / "instruments.csv"
    issues: list[ValidationIssue] = []

    if not bars_path.is_file():
        issues.append(ValidationIssue("MISSING_BARS", "critical", f"missing {bars_path.name}"))
        return DatasetValidationReport(str(directory), str(bars_path), str(instruments_path), 0, 0,
                                       tuple(issues), tuple(), tuple(), False, 0)
    if not instruments_path.is_file():
        issues.append(ValidationIssue("MISSING_INSTRUMENTS", "critical", f"missing {instruments_path.name}"))

    instruments: dict[int, str] = {}
    instrument_meta: dict[int, dict[str, str]] = {}
    if instruments_path.is_file():
        with instruments_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            required = {"instrument_id", "canonical_symbol"}
            missing = required - set(reader.fieldnames or [])
            if missing:
                issues.append(ValidationIssue("INVALID_INSTRUMENTS", "critical",
                                              f"instruments.csv missing columns {sorted(missing)}"))
            else:
                for row_no, row in enumerate(reader, start=2):
                    try:
                        iid = int(row["instrument_id"])
                    except ValueError:
                        issues.append(ValidationIssue("INVALID_INSTRUMENT_ID", "critical",
                                                      "instrument_id is not an integer", row=row_no))
                        continue
                    symbol = row["canonical_symbol"].strip()
                    instrument_meta[iid] = {str(k): str(v).strip() for k, v in row.items() if k}
                    if not symbol:
                        issues.append(ValidationIssue("EMPTY_SYMBOL", "critical",
                                                      "canonical_symbol is empty", instrument_id=iid, row=row_no))
                    if iid in instruments:
                        issues.append(ValidationIssue("DUPLICATE_INSTRUMENT", "critical",
                                                      f"duplicate instrument_id {iid}", instrument_id=iid, row=row_no))
                    instruments[iid] = symbol

    groups: dict[tuple[int, int], list[tuple[int, datetime]]] = defaultdict(list)
    raw_previous: dict[tuple[int, int], datetime] = {}
    previous_close: dict[tuple[int, int], Decimal] = {}
    warmup_seen: dict[tuple[int, int], bool] = defaultdict(bool)
    warmup_closed: dict[tuple[int, int], bool] = defaultdict(bool)
    warmup_groups: set[tuple[int, int]] = set()
    rows = 0
    checked_intervals: set[int] = set()
    synthetic = False
    warmup_rows = 0

    with bars_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = set(REQUIRED_COLUMNS) - set(reader.fieldnames or [])
        if missing:
            issues.append(ValidationIssue("MISSING_BAR_COLUMNS", "critical",
                                          f"bars.csv missing columns {sorted(missing)}"))
            return DatasetValidationReport(str(directory), str(bars_path), str(instruments_path), 0, 0,
                                           tuple(issues), tuple(), tuple(instruments.values()), False, 0)

        synthetic = "synthetic" in str(reader.fieldnames).lower() or (
            (directory / "manifest.json").is_file()
            and "synthetic" in (directory / "manifest.json").read_text(encoding="utf-8").lower()
        )

        for row_no, row in enumerate(reader, start=2):
            rows += 1
            try:
                iid = int(row["instrument_id"])
                interval = int(row["interval_seconds"])
                start = _parse_ts(row["bar_start"])
                end = _parse_ts(row["bar_end"])
            except (ValueError, KeyError) as exc:
                issues.append(ValidationIssue("INVALID_BAR_HEADER_DATA", "critical",
                                              f"unparseable bar identity/time: {exc}", row=row_no))
                continue

            checked_intervals.add(interval)
            if interval <= 0:
                issues.append(ValidationIssue("INVALID_INTERVAL", "critical",
                                              "interval_seconds must be positive", iid, interval, row_no))
            if interval_seconds is not None and interval != interval_seconds:
                issues.append(ValidationIssue("INTERVAL_MISMATCH", "critical",
                                              f"expected interval {interval_seconds}, got {interval}",
                                              iid, interval, row_no))
            if iid not in instruments:
                issues.append(ValidationIssue("UNKNOWN_INSTRUMENT", "critical",
                                              f"instrument_id {iid} is absent from instruments.csv",
                                              iid, interval, row_no))

            if end - start != timedelta(seconds=interval):
                issues.append(ValidationIssue("BAR_DURATION", "critical",
                                              "bar_end is not bar_start + interval",
                                              iid, interval, row_no))
            if start.tzinfo is None or end.tzinfo is None:
                issues.append(ValidationIssue("NAIVE_TIMESTAMP", "critical",
                                              "bar timestamps must include timezone",
                                              iid, interval, row_no))
            if end <= start:
                issues.append(ValidationIssue("INVALID_TIME_RANGE", "critical",
                                              "bar_end must be after bar_start",
                                              iid, interval, row_no))

            meta = instrument_meta.get(iid, {})
            for flag in ("tradable", "eligible"):
                if flag in meta and meta[flag].lower() in {"false", "0", "no", "disabled"}:
                    issues.append(ValidationIssue(
                        "INSTRUMENT_NOT_ELIGIBLE", "critical",
                        f"{flag} is disabled for {meta.get('canonical_symbol', iid)}",
                        iid, interval, row_no,
                    ))
            if profile_map and row.get("canonical_symbol") and row["canonical_symbol"].strip() not in profile_map:
                issues.append(ValidationIssue(
                    "UNPROFILED_SYMBOL", "critical",
                    f"no market profile configured for {row['canonical_symbol'].strip()}",
                    iid, interval, row_no,
                ))

            values = [_decimal(row[name], name, row_no, issues)
                      for name in ("open_price", "high_price", "low_price", "close_price", "volume")]
            if all(v is not None for v in values):
                open_, high, low, close, volume = values
                assert open_ is not None and high is not None and low is not None and close is not None and volume is not None
                if min(open_, high, low, close) <= 0:
                    issues.append(ValidationIssue("NON_POSITIVE_PRICE", "critical",
                                                  "OHLC prices must be positive", iid, interval, row_no))
                if high < max(open_, close) or low > min(open_, close) or high < low:
                    issues.append(ValidationIssue("OHLC_BOUNDS", "critical",
                                                  "OHLC bounds are invalid", iid, interval, row_no))
                if volume < 0:
                    issues.append(ValidationIssue("NEGATIVE_VOLUME", "critical",
                                                  "volume cannot be negative", iid, interval, row_no))
                previous = previous_close.get((iid, interval))
                if previous is not None and previous > 0:
                    jump = abs(close / previous - Decimal("1"))
                    if jump >= Decimal("0.25"):
                        issues.append(ValidationIssue(
                            "LARGE_PRICE_JUMP", "warning",
                            f"adjacent close changed {jump * 100:.2f}%; verify split/corporate-action adjustment",
                            iid, interval, row_no,
                        ))
                previous_close[(iid, interval)] = close

            complete = row.get("is_complete", "").strip().lower()
            if complete not in {"true", "false", "1", "0", "yes", "no"}:
                issues.append(ValidationIssue("INVALID_COMPLETENESS", "critical",
                                              "is_complete must be boolean-like", iid, interval, row_no))
            elif complete in {"false", "0", "no"}:
                issues.append(ValidationIssue("INCOMPLETE_BAR", "critical",
                                              "incomplete bars cannot be scored by the backtest",
                                              iid, interval, row_no))

            if row.get("quality_status", "").strip().lower() in {"bad", "invalid", "rejected"}:
                issues.append(ValidationIssue("BAD_QUALITY", "critical",
                                              "bar quality_status is not usable", iid, interval, row_no))

            key = (iid, interval)
            previous = raw_previous.get(key)
            if previous is not None and start < previous:
                issues.append(ValidationIssue("OUT_OF_ORDER", "critical",
                                              "bars are out of chronological order in source file",
                                              iid, interval, row_no))
            raw_previous[key] = start
            groups[key].append((row_no, start))

            is_warmup = row.get("is_warmup", "").strip().lower() in {"true", "1", "yes"}
            key = (iid, interval)
            if is_warmup:
                warmup_rows += 1
                warmup_groups.add(key)
                if warmup_closed[key]:
                    issues.append(ValidationIssue(
                        "WARMUP_AFTER_SCORED", "critical",
                        "warmup bars must precede all scored bars", iid, interval, row_no,
                    ))
                warmup_seen[key] = True
            elif warmup_seen[key]:
                warmup_closed[key] = True

    # Duplicate timestamps and internal gaps are evaluated after parsing so source-order errors
    # cannot be hidden by sorting.
    for (iid, interval), entries in groups.items():
        starts = [x[1] for x in entries]
        seen: set[datetime] = set()
        for row_no, start in entries:
            if start in seen:
                issues.append(ValidationIssue("DUPLICATE_BAR", "critical",
                                              "duplicate bar_start within instrument/timeframe",
                                              iid, interval, row_no))
            seen.add(start)
        ordered = sorted(starts)
        for prev, current in zip(ordered, ordered[1:]):
            delta = current - prev
            if delta < timedelta(seconds=interval):
                continue
            if delta > timedelta(seconds=interval):
                # Gaps are warnings by default because regular market sessions naturally contain
                # overnight/weekend/holiday gaps. The scored replay must surface them explicitly.
                issues.append(ValidationIssue("DATA_GAP", "warning",
                                              f"gap of {int(delta.total_seconds())} seconds between bars",
                                              iid, interval))

    for iid, meta in instrument_meta.items():
        mode = meta.get("adjustment_mode") or meta.get("corporate_action_mode")
        if mode and mode.lower() not in CORPORATE_ACTION_MODES:
            issues.append(ValidationIssue(
                "INVALID_ADJUSTMENT_MODE", "critical",
                f"unsupported corporate-action adjustment mode: {mode}", iid,
            ))
        elif not mode:
            # Missing metadata is allowed for legacy/local fixtures; a suspicious price jump
            # above is the actionable signal that requires adjustment review.
            pass

    manifest_path = directory / "manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            source = str(manifest.get("source", "")).lower()
            synthetic = synthetic or "synthetic" in source or "synthetic" in str(manifest.get("fixture", "")).lower()
            if manifest.get("redistribution_allowed") is False:
                issues.append(ValidationIssue("REDISTRIBUTION_RESTRICTED", "warning",
                                              "dataset manifest is not marked redistribution_allowed"))
        except json.JSONDecodeError:
            issues.append(ValidationIssue("INVALID_MANIFEST", "critical", "manifest.json is not valid JSON"))

    symbols = tuple(sorted({s for s in instruments.values() if s}))
    if profile_map and require_profile:
        for symbol in symbols:
            if symbol not in profile_map:
                issues.append(ValidationIssue("UNPROFILED_SYMBOL", "critical",
                                              f"no market profile configured for {symbol}"))

    if fail_on_warnings and any(i.severity == "warning" for i in issues):
        issues.extend(
            ValidationIssue(i.code, "critical", f"warning promoted: {i.message}",
                            i.instrument_id, i.interval_seconds, i.row)
            for i in issues if i.severity == "warning"
        )

    return DatasetValidationReport(
        str(directory), str(bars_path), str(instruments_path), rows, len(groups),
        tuple(issues), tuple(sorted(checked_intervals)), symbols, synthetic, warmup_rows, len(warmup_groups),
    )
