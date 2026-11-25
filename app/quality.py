"""Market-data quality checks and findings."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class QualityFinding:
    code: str
    message: str
    affected_start: datetime | None = None
    affected_end: datetime | None = None


class QualityChecker:
    def __init__(
        self,
        symbol_mapping: Mapping[str, str] | None = None,
        *,
        max_sequence_gap: int | None = None,
        max_timestamp_gap_seconds: float | None = None,
    ) -> None:
        self.symbol_mapping = dict(symbol_mapping or {})
        self.max_sequence_gap = max_sequence_gap
        self.max_timestamp_gap_seconds = max_timestamp_gap_seconds

    def check(self, events: Iterable[object], *, source_run_id: str | None = None) -> list[QualityFinding]:
        findings: list[QualityFinding] = []
        seen: set[str] = set()
        previous_time: datetime | None = None
        previous_sequence: int | None = None
        for event in events:
            event_time = getattr(event, "event_time", None)
            canonical = getattr(event, "canonical_symbol", None)
            venue_symbol = getattr(event, "venue_symbol", None)
            venue_id = getattr(event, "venue_id", None)
            dedupe = getattr(event, "deduplication_key", None)
            sequence = getattr(event, "sequence_no", None)
            price = getattr(event, "price", None)
            quantity = getattr(event, "quantity", None)

            if canonical is None:
                findings.append(QualityFinding("missing_required_field", "missing canonical_symbol"))
            if not venue_symbol:
                findings.append(QualityFinding("missing_required_field", "missing venue_symbol"))
            if not venue_id:
                findings.append(QualityFinding("missing_required_field", "missing venue_id"))
            if not isinstance(event_time, datetime):
                findings.append(QualityFinding("invalid_timestamp", "event_time is not a datetime", None, None))
            elif event_time.tzinfo is None:
                findings.append(QualityFinding("invalid_timestamp", "event_time is not timezone-aware", event_time, event_time))

            for name, value in (("price", price), ("quantity", quantity)):
                if value is None:
                    findings.append(QualityFinding("missing_required_field", f"missing {name}"))
                elif not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                    findings.append(QualityFinding("non_positive_value", f"{name} must be positive"))

            if venue_symbol is not None:
                mapped = self.symbol_mapping.get(str(venue_symbol))
                if canonical is not None and mapped is not None and canonical != mapped:
                    findings.append(QualityFinding("symbol_mapping", f"{venue_symbol} maps to {mapped}, not {canonical}"))

            if dedupe:
                if dedupe in seen:
                    findings.append(QualityFinding("duplicate", f"duplicate dedupe key {dedupe}"))
                seen.add(str(dedupe))

            if isinstance(event_time, datetime) and event_time.tzinfo is not None:
                if previous_time is not None:
                    if event_time < previous_time:
                        findings.append(QualityFinding("out_of_order", "event time moved backwards", event_time, previous_time))
                    elif self.max_timestamp_gap_seconds is not None:
                        gap = (event_time - previous_time).total_seconds()
                        if gap > self.max_timestamp_gap_seconds:
                            findings.append(QualityFinding("timestamp_gap", f"timestamp gap {gap:g}s", previous_time, event_time))
                previous_time = event_time

            if isinstance(sequence, int):
                if previous_sequence is not None:
                    gap = sequence - previous_sequence
                    if gap < 0:
                        findings.append(QualityFinding("out_of_order", "sequence moved backwards"))
                    elif self.max_sequence_gap is not None and gap > self.max_sequence_gap:
                        findings.append(QualityFinding("sequence_gap", f"sequence gap {gap}", previous_time, previous_time))
                previous_sequence = sequence
        return findings
