"""Bronze-layer normalization for Phase 1 CSV inputs."""

from __future__ import annotations

import csv
from datetime import timezone
from decimal import Decimal
from pathlib import Path

from microalpha.data.schemas import parse_decimal, parse_timestamp, schema_for


def _timestamp_to_utc_iso(value: str, *, source_timezone: str, column: str) -> str:
    parsed = parse_timestamp(value, column=column)
    if parsed.tzinfo is None:
        if source_timezone != "UTC":
            raise ValueError(
                "Naive timestamps are only supported when source_timezone is UTC in Phase 1"
            )
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _decimal_to_text(value: str, *, column: str) -> str:
    parsed = parse_decimal(value, column=column)
    return format(parsed.normalize() if parsed == parsed.to_integral() else parsed, "f")


def normalize_csv_to_bronze(
    *,
    raw_path: str | Path,
    bronze_path: str | Path,
    dataset_type: str,
    source_timezone: str,
    source_checksum: str,
    instrument: str,
) -> int:
    """Normalize a copied raw CSV into a bronze CSV artifact.

    Raw files are not modified. Bronze normalization standardizes timestamp,
    numeric, side, and update-type fields while preserving all practical source
    fields and adding source lineage columns.
    """

    schema = schema_for(dataset_type)
    raw_csv = Path(raw_path)
    bronze_csv = Path(bronze_path)
    bronze_csv.parent.mkdir(parents=True, exist_ok=True)

    with raw_csv.open("r", encoding="utf-8", newline="") as source_file:
        reader = csv.DictReader(source_file)
        if reader.fieldnames is None:
            raise ValueError(f"CSV file has no header: {raw_csv}")

        output_fields = list(reader.fieldnames)
        for column in ("event_time", "receive_time"):
            if column in output_fields:
                original_column = f"source_{column}"
                if original_column not in output_fields:
                    output_fields.append(original_column)
        for lineage_column in ("instrument", "source_checksum"):
            if lineage_column not in output_fields:
                output_fields.append(lineage_column)

        row_count = 0
        with bronze_csv.open("w", encoding="utf-8", newline="") as bronze_file:
            writer = csv.DictWriter(bronze_file, fieldnames=output_fields, extrasaction="ignore")
            writer.writeheader()
            for row in reader:
                normalized = dict(row)
                for column in ("event_time", "receive_time"):
                    if column in normalized and normalized[column] not in (None, ""):
                        normalized[f"source_{column}"] = normalized[column]
                        normalized[column] = _timestamp_to_utc_iso(
                            normalized[column],
                            source_timezone=source_timezone,
                            column=column,
                        )
                for column in schema.price_columns + schema.quantity_columns:
                    if column in normalized and normalized[column] not in (None, ""):
                        normalized[column] = _decimal_to_text(normalized[column], column=column)
                if "sequence_id" in normalized and normalized["sequence_id"] not in (None, ""):
                    normalized["sequence_id"] = str(int(Decimal(normalized["sequence_id"])))
                if "side" in normalized and normalized["side"] not in (None, ""):
                    normalized["side"] = normalized["side"].strip().lower()
                if "update_type" in normalized and normalized["update_type"] not in (None, ""):
                    normalized["update_type"] = normalized["update_type"].strip().lower()
                normalized["instrument"] = instrument
                normalized["source_checksum"] = source_checksum
                writer.writerow(normalized)
                row_count += 1
    return row_count
