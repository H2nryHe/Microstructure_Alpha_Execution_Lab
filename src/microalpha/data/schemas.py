"""Phase 1 raw market-data schema definitions and validation."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Optional


class DataValidationError(ValueError):
    """Raised when raw market data violates a required ingestion contract."""


@dataclass(frozen=True)
class DatasetSchema:
    name: str
    required_columns: tuple[str, ...]
    price_columns: tuple[str, ...]
    quantity_columns: tuple[str, ...]
    timestamp_columns: tuple[str, ...] = ("event_time", "receive_time")
    optional_numeric_columns: tuple[str, ...] = ("sequence_id",)


SCHEMAS: dict[str, DatasetSchema] = {
    "trades": DatasetSchema(
        name="trades",
        required_columns=("event_time", "price", "quantity"),
        price_columns=("price",),
        quantity_columns=("quantity",),
    ),
    "book_updates": DatasetSchema(
        name="book_updates",
        required_columns=("event_time", "side", "price", "quantity"),
        price_columns=("price",),
        quantity_columns=("quantity",),
    ),
    "snapshots": DatasetSchema(
        name="snapshots",
        required_columns=("event_time",),
        price_columns=tuple(
            f"{side}_px_{level}" for side in ("bid", "ask") for level in range(1, 11)
        ),
        quantity_columns=tuple(
            f"{side}_sz_{level}" for side in ("bid", "ask") for level in range(1, 11)
        ),
    ),
}


def schema_for(dataset_type: str) -> DatasetSchema:
    try:
        return SCHEMAS[dataset_type]
    except KeyError as exc:
        supported = ", ".join(sorted(SCHEMAS))
        raise DataValidationError(
            f"Unsupported dataset_type={dataset_type!r}; supported: {supported}"
        ) from exc


def parse_timestamp(value: str, *, column: str) -> datetime:
    if value is None or not value.strip():
        raise DataValidationError(f"Missing timestamp in column {column}")
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise DataValidationError(
            f"Could not parse timestamp {value!r} in column {column}"
        ) from exc


def parse_decimal(value: str, *, column: str) -> Decimal:
    if value is None or not value.strip():
        raise DataValidationError(f"Missing numeric value in column {column}")
    try:
        return Decimal(value.strip())
    except InvalidOperation as exc:
        raise DataValidationError(
            f"Could not parse numeric value {value!r} in column {column}"
        ) from exc


def _present_columns(row: dict[str, str], columns: Iterable[str]) -> Iterable[str]:
    for column in columns:
        if column in row and row[column] not in (None, ""):
            yield column


def validate_csv_rows(path: str | Path, dataset_type: str) -> int:
    """Validate Phase 1 CSV schema and basic types.

    Returns the number of data rows when validation succeeds.
    """

    csv_path = Path(path)
    schema = schema_for(dataset_type)

    with csv_path.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames is None:
            raise DataValidationError(f"CSV file has no header: {csv_path}")

        missing = sorted(set(schema.required_columns) - set(reader.fieldnames))
        if missing:
            raise DataValidationError(f"Missing required columns for {dataset_type}: {missing}")

        row_count = 0
        for row_count, row in enumerate(reader, start=1):
            for column in schema.timestamp_columns:
                if column in row and row[column] not in (None, ""):
                    parse_timestamp(row[column], column=column)

            for column in _present_columns(row, schema.price_columns):
                value = parse_decimal(row[column], column=column)
                if value <= 0:
                    raise DataValidationError(f"Column {column} must be > 0 at row {row_count}")

            for column in _present_columns(row, schema.quantity_columns):
                value = parse_decimal(row[column], column=column)
                if value < 0:
                    raise DataValidationError(f"Column {column} must be >= 0 at row {row_count}")

            for column in _present_columns(row, schema.optional_numeric_columns):
                value = parse_decimal(row[column], column=column)
                if value != value.to_integral_value():
                    raise DataValidationError(
                        f"Column {column} must be an integer at row {row_count}"
                    )

    return row_count


def required_columns(dataset_type: str) -> tuple[str, ...]:
    return schema_for(dataset_type).required_columns


def optional_column(fieldnames: Optional[list[str]], column: str) -> bool:
    return fieldnames is not None and column in fieldnames
