"""Phase 1 raw market-data ingestion."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

from microalpha.data.normalize import normalize_csv_to_bronze
from microalpha.data.schemas import DataValidationError, required_columns, validate_csv_rows
from microalpha.utils.time import utc_now_iso


@dataclass(frozen=True)
class IngestionResult:
    dataset_type: str
    instrument: str
    trade_date: str
    raw_path: str
    bronze_path: str
    manifest_path: str
    source_checksum: str
    row_count: int
    copied_raw: bool


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_name(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in "._-" else "_" for character in value
    )


def _copy_raw_immutable(source_path: Path, destination_path: Path, checksum: str) -> bool:
    if destination_path.exists():
        existing_checksum = sha256_file(destination_path)
        if existing_checksum != checksum:
            raise DataValidationError(
                f"Existing raw file checksum mismatch at {destination_path}: "
                f"{existing_checksum} != {checksum}"
            )
        return False
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_path, destination_path)
    copied_checksum = sha256_file(destination_path)
    if copied_checksum != checksum:
        raise DataValidationError(
            f"Copied raw file checksum mismatch: {copied_checksum} != {checksum}"
        )
    return True


def ingest_csv(
    *,
    source_path: str | Path,
    dataset_type: str,
    instrument: str,
    trade_date: str,
    source_timezone: str = "UTC",
    source_name: str = "local_csv",
    raw_dir: str | Path = "data/raw",
    bronze_dir: str | Path = "data/bronze",
    manifest_dir: str | Path = "data/manifests",
    source_metadata: Optional[dict[str, Any]] = None,
) -> IngestionResult:
    """Ingest a raw CSV file and create a normalized bronze CSV.

    The raw file is validated, copied byte-for-byte into a checksum-addressed
    destination, and never modified afterward. Re-ingesting the same source
    checksum reuses the existing raw and bronze paths rather than duplicating
    data.
    """

    source_csv = Path(source_path)
    if not source_csv.exists():
        raise FileNotFoundError(f"Source file does not exist: {source_csv}")
    if source_timezone != "UTC":
        raise DataValidationError("Phase 1 currently supports UTC source timezone only")

    row_count = validate_csv_rows(source_csv, dataset_type)
    checksum = sha256_file(source_csv)
    checksum_prefix = checksum[:16]
    safe_instrument = _safe_name(instrument)
    safe_date = _safe_name(trade_date)
    safe_source = _safe_name(source_name)
    safe_filename = _safe_name(source_csv.name)

    raw_path = (
        Path(raw_dir)
        / safe_source
        / safe_instrument
        / safe_date
        / dataset_type
        / f"{checksum_prefix}-{safe_filename}"
    )
    bronze_path = (
        Path(bronze_dir)
        / safe_source
        / safe_instrument
        / safe_date
        / dataset_type
        / f"{checksum_prefix}.csv"
    )
    manifest_path = (
        Path(manifest_dir)
        / "phase1"
        / safe_source
        / safe_instrument
        / safe_date
        / dataset_type
        / f"{checksum_prefix}.json"
    )

    copied_raw = _copy_raw_immutable(source_csv, raw_path, checksum)
    bronze_row_count = normalize_csv_to_bronze(
        raw_path=raw_path,
        bronze_path=bronze_path,
        dataset_type=dataset_type,
        source_timezone=source_timezone,
        source_checksum=checksum,
        instrument=instrument,
    )
    if bronze_row_count != row_count:
        raise DataValidationError(
            f"Bronze row count mismatch: raw={row_count}, bronze={bronze_row_count}"
        )

    manifest = {
        "dataset_type": dataset_type,
        "instrument": instrument,
        "trade_date": trade_date,
        "source_name": source_name,
        "source_timezone": source_timezone,
        "source_path": str(source_csv),
        "source_filename": source_csv.name,
        "raw_path": str(raw_path),
        "bronze_path": str(bronze_path),
        "source_checksum_sha256": checksum,
        "ingested_at": utc_now_iso(),
        "row_count": row_count,
        "required_columns": list(required_columns(dataset_type)),
        "source_metadata": source_metadata or {},
        "raw_copy_created": copied_raw,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    return IngestionResult(
        dataset_type=dataset_type,
        instrument=instrument,
        trade_date=trade_date,
        raw_path=str(raw_path),
        bronze_path=str(bronze_path),
        manifest_path=str(manifest_path),
        source_checksum=checksum,
        row_count=row_count,
        copied_raw=copied_raw,
    )


def result_to_dict(result: IngestionResult) -> dict[str, Any]:
    return asdict(result)
