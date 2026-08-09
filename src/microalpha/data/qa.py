"""Phase 2 market-data QA validators."""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional

from microalpha.config import load_yaml_config

ERROR = "ERROR"
WARNING = "WARNING"


class QAContinuationError(RuntimeError):
    """Raised when downstream processing is attempted after critical QA errors."""


@dataclass(frozen=True)
class QAIssue:
    severity: str
    validator: str
    message: str
    row_number: Optional[int] = None
    field: Optional[str] = None


@dataclass
class QAReport:
    status: str
    can_continue: bool
    dataset_type: str
    row_count: int
    duplicate_count: int = 0
    sequence_gap_count: int = 0
    crossed_book_count: int = 0
    locked_book_count: int = 0
    timestamp_error_count: int = 0
    warning_count: int = 0
    error_count: int = 0
    issues: list[QAIssue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_issue(
        self,
        *,
        severity: str,
        validator: str,
        message: str,
        row_number: Optional[int] = None,
        field: Optional[str] = None,
    ) -> None:
        issue = QAIssue(
            severity=severity,
            validator=validator,
            message=message,
            row_number=row_number,
            field=field,
        )
        self.issues.append(issue)
        if severity == ERROR:
            self.error_count += 1
            self.status = "FAIL"
            self.can_continue = False
        else:
            self.warning_count += 1
            self.warnings.append(message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "can_continue": self.can_continue,
            "dataset_type": self.dataset_type,
            "row_count": self.row_count,
            "duplicate_count": self.duplicate_count,
            "sequence_gap_count": self.sequence_gap_count,
            "crossed_book_count": self.crossed_book_count,
            "locked_book_count": self.locked_book_count,
            "timestamp_error_count": self.timestamp_error_count,
            "warning_count": self.warning_count,
            "error_count": self.error_count,
            "warnings": sorted(self.warnings),
            "issues": [asdict(issue) for issue in self.issues],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"


def load_qa_config(path: str | Path = "configs/qa.yaml") -> dict[str, Any]:
    return load_yaml_config(path)


def assert_can_continue(report: QAReport) -> None:
    if not report.can_continue:
        raise QAContinuationError("QA report contains ERROR issues; downstream processing blocked")


def write_qa_report(report: QAReport, path: str | Path) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report.to_json(), encoding="utf-8")
    return output_path


def _parse_timestamp(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    return datetime.fromisoformat(normalized)


def _parse_decimal(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid decimal value: {value}") from exc


def _decimal_or_none(row: dict[str, str], *columns: str) -> Optional[Decimal]:
    for column in columns:
        value = row.get(column)
        if value not in (None, ""):
            return _parse_decimal(value)
    return None


def _bbo(row: dict[str, str]) -> tuple[Optional[Decimal], Optional[Decimal]]:
    bid = _decimal_or_none(row, "best_bid", "bid_px_1", "bid_price")
    ask = _decimal_or_none(row, "best_ask", "ask_px_1", "ask_price")
    return bid, ask


def _has_bbo(row: dict[str, str]) -> bool:
    bid, ask = _bbo(row)
    return bid is not None and ask is not None


def _observed_price(row: dict[str, str]) -> Optional[Decimal]:
    bid, ask = _bbo(row)
    if bid is not None and ask is not None:
        return (bid + ask) / Decimal("2")
    return _decimal_or_none(row, "price")


def _event_gap_ms(previous: datetime, current: datetime) -> Decimal:
    return Decimal(str((current - previous).total_seconds() * 1000))


def _row_fingerprint(row: dict[str, str], fieldnames: list[str]) -> bytes:
    digest = hashlib.sha256()
    for column in fieldnames:
        value = row.get(column, "")
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return digest.digest()


def validate_market_data_csv(
    path: str | Path,
    *,
    dataset_type: str,
    config: Optional[dict[str, Any]] = None,
    order_timestamp_column: str = "event_time",
) -> QAReport:
    """Run Phase 2 QA checks on a normalized market-data CSV file."""

    qa_config = config or load_qa_config()
    report = QAReport(status="PASS", can_continue=True, dataset_type=dataset_type, row_count=0)
    seen_rows: set[bytes] = set()
    previous_event_time: Optional[datetime] = None
    previous_sequence: Optional[int] = None
    previous_price: Optional[Decimal] = None
    previous_bbo: Optional[tuple[Decimal, Decimal]] = None
    stale_bbo_start: Optional[datetime] = None
    stale_bbo_reported = False

    min_event_time = _parse_timestamp(qa_config["timestamp"]["min_event_time"])
    max_event_time = _parse_timestamp(qa_config["timestamp"]["max_event_time"])
    price_jump_threshold = Decimal(str(qa_config["price"]["discontinuity_bps_error"]))
    max_quantity = Decimal(str(qa_config["size"]["absolute_quantity_error"]))
    max_gap_ms = Decimal(str(qa_config["gaps"]["max_inter_update_gap_ms_warning"]))
    stale_bbo_ms = Decimal(str(qa_config["staleness"]["stale_bbo_ms_warning"]))

    with Path(path).open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        fieldnames = reader.fieldnames or []
        for row_number, row in enumerate(reader, start=2):
            report.row_count += 1
            row_key = _row_fingerprint(row, fieldnames)
            if row_key in seen_rows:
                report.duplicate_count += 1
                report.add_issue(
                    severity=WARNING,
                    validator="exact_duplicate",
                    message=f"Exact duplicate row detected at row {row_number}",
                    row_number=row_number,
                )
            seen_rows.add(row_key)

            event_time = _validate_timestamp(
                row,
                row_number=row_number,
                report=report,
                min_event_time=min_event_time,
                max_event_time=max_event_time,
            )
            order_time = event_time
            if order_timestamp_column != "event_time":
                order_time = _validate_optional_order_timestamp(
                    row,
                    column=order_timestamp_column,
                    row_number=row_number,
                    report=report,
                    min_event_time=min_event_time,
                    max_event_time=max_event_time,
                )
            if order_time is not None and previous_event_time is not None:
                if order_time < previous_event_time:
                    report.timestamp_error_count += 1
                    report.add_issue(
                        severity=ERROR,
                        validator="backward_timestamp",
                        message=(
                            f"{order_timestamp_column} moved backward at row {row_number}"
                        ),
                        row_number=row_number,
                        field=order_timestamp_column,
                    )
                gap_ms = _event_gap_ms(previous_event_time, order_time)
                if gap_ms > max_gap_ms:
                    report.add_issue(
                        severity=WARNING,
                        validator="update_gap",
                        message=f"Inter-update gap {gap_ms} ms exceeds {max_gap_ms} ms",
                        row_number=row_number,
                        field="event_time",
                    )
            if order_time is not None:
                previous_event_time = order_time

            previous_sequence = _validate_sequence(
                row,
                row_number=row_number,
                previous_sequence=previous_sequence,
                report=report,
            )
            _validate_price_and_size(
                row,
                row_number=row_number,
                report=report,
                max_quantity=max_quantity,
            )
            _validate_book_state(row, row_number=row_number, report=report)
            if dataset_type != "book_updates" or _has_bbo(row):
                previous_price = _validate_price_discontinuity(
                    row,
                    row_number=row_number,
                    previous_price=previous_price,
                    report=report,
                    threshold_bps=price_jump_threshold,
                )
            previous_bbo, stale_bbo_start, stale_bbo_reported = _validate_stale_bbo(
                row,
                event_time=event_time,
                row_number=row_number,
                previous_bbo=previous_bbo,
                stale_bbo_start=stale_bbo_start,
                stale_bbo_reported=stale_bbo_reported,
                report=report,
                stale_bbo_ms=stale_bbo_ms,
            )

    return report


def _validate_timestamp(
    row: dict[str, str],
    *,
    row_number: int,
    report: QAReport,
    min_event_time: datetime,
    max_event_time: datetime,
) -> Optional[datetime]:
    value = row.get("event_time")
    if value in (None, ""):
        report.timestamp_error_count += 1
        report.add_issue(
            severity=ERROR,
            validator="missing_timestamp",
            message=f"Missing event_time at row {row_number}",
            row_number=row_number,
            field="event_time",
        )
        return None
    try:
        event_time = _parse_timestamp(value)
    except ValueError:
        report.timestamp_error_count += 1
        report.add_issue(
            severity=ERROR,
            validator="corrupted_timestamp",
            message=f"Could not parse event_time at row {row_number}",
            row_number=row_number,
            field="event_time",
        )
        return None
    if event_time.tzinfo is None:
        report.timestamp_error_count += 1
        report.add_issue(
            severity=ERROR,
            validator="timezone_inconsistency",
            message=f"Naive event_time at row {row_number}",
            row_number=row_number,
            field="event_time",
        )
        return event_time
    if event_time < min_event_time or event_time > max_event_time:
        report.timestamp_error_count += 1
        report.add_issue(
            severity=ERROR,
            validator="impossible_timestamp",
            message=f"event_time outside configured bounds at row {row_number}",
            row_number=row_number,
            field="event_time",
        )
    return event_time


def _validate_optional_order_timestamp(
    row: dict[str, str],
    *,
    column: str,
    row_number: int,
    report: QAReport,
    min_event_time: datetime,
    max_event_time: datetime,
) -> Optional[datetime]:
    value = row.get(column)
    if value in (None, ""):
        report.timestamp_error_count += 1
        report.add_issue(
            severity=ERROR,
            validator="missing_timestamp",
            message=f"Missing {column} at row {row_number}",
            row_number=row_number,
            field=column,
        )
        return None
    try:
        order_time = _parse_timestamp(value)
    except ValueError:
        report.timestamp_error_count += 1
        report.add_issue(
            severity=ERROR,
            validator="corrupted_timestamp",
            message=f"Could not parse {column} at row {row_number}",
            row_number=row_number,
            field=column,
        )
        return None
    if order_time.tzinfo is None or order_time < min_event_time or order_time > max_event_time:
        report.timestamp_error_count += 1
        report.add_issue(
            severity=ERROR,
            validator="impossible_timestamp",
            message=f"{column} outside configured timezone/bounds at row {row_number}",
            row_number=row_number,
            field=column,
        )
    return order_time


def _validate_sequence(
    row: dict[str, str],
    *,
    row_number: int,
    previous_sequence: Optional[int],
    report: QAReport,
) -> Optional[int]:
    value = row.get("sequence_id")
    if value in (None, ""):
        return previous_sequence
    try:
        sequence = int(value)
    except ValueError:
        report.add_issue(
            severity=ERROR,
            validator="invalid_sequence_id",
            message=f"Non-integer sequence_id at row {row_number}",
            row_number=row_number,
            field="sequence_id",
        )
        return previous_sequence
    if previous_sequence is None:
        return sequence
    if sequence == previous_sequence:
        report.add_issue(
            severity=ERROR,
            validator="repeated_sequence_id",
            message=f"Repeated sequence_id {sequence} at row {row_number}",
            row_number=row_number,
            field="sequence_id",
        )
    elif sequence < previous_sequence:
        report.add_issue(
            severity=ERROR,
            validator="out_of_order_sequence_id",
            message=f"Out-of-order sequence_id {sequence} at row {row_number}",
            row_number=row_number,
            field="sequence_id",
        )
    elif sequence > previous_sequence + 1:
        report.sequence_gap_count += int(sequence - previous_sequence - 1)
        report.add_issue(
            severity=ERROR,
            validator="sequence_gap",
            message=f"Sequence gap from {previous_sequence} to {sequence} at row {row_number}",
            row_number=row_number,
            field="sequence_id",
        )
    return sequence


def _validate_price_and_size(
    row: dict[str, str],
    *,
    row_number: int,
    report: QAReport,
    max_quantity: Decimal,
) -> None:
    price_columns = (
        "price",
        "best_bid",
        "best_ask",
        "bid_px_1",
        "ask_px_1",
        "bid_price",
        "ask_price",
    )
    for column in price_columns:
        value = row.get(column)
        if value in (None, ""):
            continue
        try:
            price = _parse_decimal(value)
        except ValueError:
            report.add_issue(
                severity=ERROR,
                validator="invalid_price",
                message=f"Invalid price in {column} at row {row_number}",
                row_number=row_number,
                field=column,
            )
            continue
        if price <= 0:
            report.add_issue(
                severity=ERROR,
                validator="invalid_price",
                message=f"Non-positive price in {column} at row {row_number}",
                row_number=row_number,
                field=column,
            )

    for column in ("quantity", "bid_sz_1", "ask_sz_1", "bid_amount", "ask_amount"):
        value = row.get(column)
        if value in (None, ""):
            continue
        try:
            quantity = _parse_decimal(value)
        except ValueError:
            report.add_issue(
                severity=ERROR,
                validator="invalid_quantity",
                message=f"Invalid quantity in {column} at row {row_number}",
                row_number=row_number,
                field=column,
            )
            continue
        if quantity < 0:
            report.add_issue(
                severity=ERROR,
                validator="negative_quantity",
                message=f"Negative quantity in {column} at row {row_number}",
                row_number=row_number,
                field=column,
            )
        if quantity > max_quantity:
            report.add_issue(
                severity=WARNING,
                validator="extreme_size",
                message=f"Quantity in {column} exceeds configured threshold at row {row_number}",
                row_number=row_number,
                field=column,
            )


def _validate_book_state(row: dict[str, str], *, row_number: int, report: QAReport) -> None:
    bid, ask = _bbo(row)
    if bid is None or ask is None:
        return
    if bid > ask:
        report.crossed_book_count += 1
        report.add_issue(
            severity=ERROR,
            validator="crossed_book",
            message=f"Best bid exceeds best ask at row {row_number}",
            row_number=row_number,
        )
    elif bid == ask:
        report.locked_book_count += 1
        report.add_issue(
            severity=WARNING,
            validator="locked_book",
            message=f"Best bid equals best ask at row {row_number}",
            row_number=row_number,
        )


def _validate_price_discontinuity(
    row: dict[str, str],
    *,
    row_number: int,
    previous_price: Optional[Decimal],
    report: QAReport,
    threshold_bps: Decimal,
) -> Optional[Decimal]:
    price = _observed_price(row)
    if price is None:
        return previous_price
    if previous_price is not None and previous_price > 0:
        jump_bps = abs(price - previous_price) / previous_price * Decimal("10000")
        if jump_bps > threshold_bps:
            report.add_issue(
                severity=ERROR,
                validator="extreme_price_discontinuity",
                message=f"Price jump {jump_bps} bps exceeds {threshold_bps} bps",
                row_number=row_number,
            )
    return price


def _validate_stale_bbo(
    row: dict[str, str],
    *,
    event_time: Optional[datetime],
    row_number: int,
    previous_bbo: Optional[tuple[Decimal, Decimal]],
    stale_bbo_start: Optional[datetime],
    stale_bbo_reported: bool,
    report: QAReport,
    stale_bbo_ms: Decimal,
) -> tuple[Optional[tuple[Decimal, Decimal]], Optional[datetime], bool]:
    bid, ask = _bbo(row)
    if bid is None or ask is None or event_time is None:
        return previous_bbo, stale_bbo_start, stale_bbo_reported
    current_bbo = (bid, ask)
    if previous_bbo != current_bbo:
        return current_bbo, event_time, False
    if stale_bbo_start is not None and not stale_bbo_reported:
        stale_ms = _event_gap_ms(stale_bbo_start, event_time)
        if stale_ms > stale_bbo_ms:
            report.add_issue(
                severity=WARNING,
                validator="stale_bbo",
                message=f"BBO unchanged for {stale_ms} ms, exceeding {stale_bbo_ms} ms",
                row_number=row_number,
            )
            return current_bbo, stale_bbo_start, True
    return current_bbo, stale_bbo_start, stale_bbo_reported
