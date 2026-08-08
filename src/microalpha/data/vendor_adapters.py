"""Vendor-specific Phase 1 adapters.

The adapters isolate external schemas from downstream project schemas. Raw
vendor files are copied byte-for-byte and never modified; bronze outputs are
vendor-agnostic CSV files.
"""

from __future__ import annotations

import csv
import gzip
import json
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from microalpha.data.ingest import sha256_file
from microalpha.data.schemas import DataValidationError
from microalpha.utils.time import utc_now_iso

BINANCE_SPOT_TRADE_COLUMNS = (
    "trade_id",
    "price",
    "quantity",
    "quote_quantity",
    "time",
    "is_buyer_maker",
    "is_best_match",
)

TARDIS_INCREMENTAL_BOOK_L2_COLUMNS = (
    "exchange",
    "symbol",
    "timestamp",
    "local_timestamp",
    "is_snapshot",
    "side",
    "price",
    "amount",
)

TARDIS_TRADES_COLUMNS = (
    "exchange",
    "symbol",
    "timestamp",
    "local_timestamp",
    "id",
    "side",
    "price",
    "amount",
)


@dataclass(frozen=True)
class VendorIngestionResult:
    vendor: str
    dataset_type: str
    instrument: str
    vendor_symbol: str
    trade_date: str
    raw_path: str
    bronze_path: str
    manifest_path: str
    source_checksum: str
    vendor_checksum: str
    vendor_checksum_verified: bool
    row_count: int
    copied_raw: bool


def _safe_name(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in "._-" else "_" for character in value
    )


def _copy_immutable(source_path: Path, destination_path: Path, checksum: str) -> bool:
    if destination_path.exists():
        if sha256_file(destination_path) != checksum:
            raise DataValidationError(f"Existing immutable raw file changed: {destination_path}")
        return False
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    destination_path.write_bytes(source_path.read_bytes())
    if sha256_file(destination_path) != checksum:
        raise DataValidationError(f"Copied raw checksum mismatch for {destination_path}")
    return True


def _parse_binance_checksum(path: Path) -> str:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise DataValidationError(f"Empty Binance checksum file: {path}")
    return text.split()[0]


def _epoch_to_utc_iso(value: str, *, unit: str) -> str:
    integer_value = int(value)
    if unit == "milliseconds":
        seconds = integer_value / 1_000
    elif unit == "microseconds":
        seconds = integer_value / 1_000_000
    else:
        raise ValueError(f"Unsupported timestamp unit: {unit}")
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat(timespec="microseconds")


def _binance_time_unit_for_date(trade_date: str) -> str:
    # Binance documents Spot timestamps as microseconds from 2025-01-01 onward.
    return "microseconds" if trade_date >= "2025-01-01" else "milliseconds"


def _manifest_path(
    *,
    manifest_dir: Path,
    vendor: str,
    instrument: str,
    trade_date: str,
    dataset_type: str,
    checksum_prefix: str,
) -> Path:
    return (
        manifest_dir
        / "phase1"
        / _safe_name(vendor)
        / _safe_name(instrument)
        / _safe_name(trade_date)
        / dataset_type
        / f"{checksum_prefix}.json"
    )


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _copy_checksum_file(
    checksum_path: Optional[Path],
    destination_path: Path,
) -> Optional[str]:
    if checksum_path is None:
        return None
    checksum_text = checksum_path.read_text(encoding="utf-8")
    checksum_destination = destination_path.with_name(destination_path.name + ".CHECKSUM")
    checksum_destination.write_text(checksum_text, encoding="utf-8")
    return str(checksum_destination)


def _zip_rows(path: Path) -> Iterable[list[str]]:
    with zipfile.ZipFile(path) as archive:
        members = [member for member in archive.namelist() if not member.endswith("/")]
        if len(members) != 1:
            raise DataValidationError(f"Expected one CSV member in Binance zip, found {members}")
        with archive.open(members[0], "r") as file:
            text_file = (line.decode("utf-8") for line in file)
            yield from csv.reader(text_file)


def ingest_binance_spot_trades_zip(
    *,
    source_zip_path: str | Path,
    checksum_path: str | Path,
    instrument: str,
    vendor_symbol: str,
    trade_date: str,
    raw_dir: str | Path = "data/raw",
    bronze_dir: str | Path = "data/bronze",
    manifest_dir: str | Path = "data/manifests",
) -> VendorIngestionResult:
    """Ingest Binance official public Spot trades zip into bronze trades CSV."""

    source_zip = Path(source_zip_path)
    checksum_file = Path(checksum_path)
    source_checksum = sha256_file(source_zip)
    vendor_checksum = _parse_binance_checksum(checksum_file)
    vendor_checksum_verified = source_checksum == vendor_checksum
    if not vendor_checksum_verified:
        raise DataValidationError(
            f"Binance checksum mismatch: project={source_checksum}, vendor={vendor_checksum}"
        )

    checksum_prefix = source_checksum[:16]
    raw_path = (
        Path(raw_dir)
        / "binance_spot"
        / _safe_name(vendor_symbol)
        / _safe_name(trade_date)
        / "trades"
        / f"{checksum_prefix}-{_safe_name(source_zip.name)}"
    )
    bronze_path = (
        Path(bronze_dir)
        / "binance_spot"
        / _safe_name(instrument)
        / _safe_name(trade_date)
        / "trades"
        / f"{checksum_prefix}.csv"
    )
    manifest_path = _manifest_path(
        manifest_dir=Path(manifest_dir),
        vendor="binance_spot",
        instrument=instrument,
        trade_date=trade_date,
        dataset_type="trades",
        checksum_prefix=checksum_prefix,
    )

    copied_raw = _copy_immutable(source_zip, raw_path, source_checksum)
    checksum_copy_path = _copy_checksum_file(checksum_file, raw_path)

    timestamp_unit = _binance_time_unit_for_date(trade_date)
    bronze_path.parent.mkdir(parents=True, exist_ok=True)
    row_count = 0
    with bronze_path.open("w", encoding="utf-8", newline="") as bronze_file:
        fieldnames = [
            "event_time",
            "receive_time",
            "price",
            "quantity",
            "side",
            "trade_id",
            "source_trade_id",
            "source_time",
            "quote_quantity",
            "is_buyer_maker",
            "is_best_match",
            "instrument",
            "source_checksum",
        ]
        writer = csv.DictWriter(bronze_file, fieldnames=fieldnames)
        writer.writeheader()
        for row in _zip_rows(raw_path):
            if len(row) != len(BINANCE_SPOT_TRADE_COLUMNS):
                raise DataValidationError(
                    f"Unexpected Binance trade row width {len(row)}; expected "
                    f"{len(BINANCE_SPOT_TRADE_COLUMNS)}"
                )
            values = {
                column: row[index] for index, column in enumerate(BINANCE_SPOT_TRADE_COLUMNS)
            }
            side = "sell" if values["is_buyer_maker"].lower() == "true" else "buy"
            writer.writerow(
                {
                    "event_time": _epoch_to_utc_iso(values["time"], unit=timestamp_unit),
                    "receive_time": "",
                    "price": values["price"],
                    "quantity": values["quantity"],
                    "side": side,
                    "trade_id": values["trade_id"],
                    "source_trade_id": values["trade_id"],
                    "source_time": values["time"],
                    "quote_quantity": values["quote_quantity"],
                    "is_buyer_maker": values["is_buyer_maker"],
                    "is_best_match": values["is_best_match"],
                    "instrument": instrument,
                    "source_checksum": source_checksum,
                }
            )
            row_count += 1

    manifest = {
        "vendor": "binance_spot",
        "vendor_symbol": vendor_symbol,
        "instrument": instrument,
        "dataset_type": "trades",
        "trade_date": trade_date,
        "source_schema": list(BINANCE_SPOT_TRADE_COLUMNS),
        "normalized_schema": fieldnames,
        "timestamp_unit": timestamp_unit,
        "timezone": "UTC",
        "source_zip_path": str(source_zip),
        "source_checksum_path": str(checksum_file),
        "raw_path": str(raw_path),
        "raw_checksum_copy_path": checksum_copy_path,
        "bronze_path": str(bronze_path),
        "source_checksum_sha256": source_checksum,
        "vendor_checksum_sha256": vendor_checksum,
        "vendor_checksum_verified": vendor_checksum_verified,
        "row_count": row_count,
        "ingested_at": utc_now_iso(),
    }
    _write_manifest(manifest_path, manifest)

    return VendorIngestionResult(
        vendor="binance_spot",
        dataset_type="trades",
        instrument=instrument,
        vendor_symbol=vendor_symbol,
        trade_date=trade_date,
        raw_path=str(raw_path),
        bronze_path=str(bronze_path),
        manifest_path=str(manifest_path),
        source_checksum=source_checksum,
        vendor_checksum=vendor_checksum,
        vendor_checksum_verified=vendor_checksum_verified,
        row_count=row_count,
        copied_raw=copied_raw,
    )


def ingest_tardis_incremental_l2_gzip(
    *,
    source_gzip_path: str | Path,
    instrument: str,
    vendor_symbol: str,
    trade_date: str,
    vendor: str = "tardis_binance_spot",
    raw_dir: str | Path = "data/raw",
    bronze_dir: str | Path = "data/bronze",
    manifest_dir: str | Path = "data/manifests",
    max_rows: Optional[int] = None,
) -> VendorIngestionResult:
    """Ingest Tardis normalized incremental_book_L2 gzip into bronze book updates."""

    source_gzip = Path(source_gzip_path)
    source_checksum = sha256_file(source_gzip)
    checksum_prefix = source_checksum[:16]
    raw_path = (
        Path(raw_dir)
        / _safe_name(vendor)
        / _safe_name(vendor_symbol)
        / _safe_name(trade_date)
        / "book_updates"
        / f"{checksum_prefix}-{_safe_name(source_gzip.name)}"
    )
    bronze_path = (
        Path(bronze_dir)
        / _safe_name(vendor)
        / _safe_name(instrument)
        / _safe_name(trade_date)
        / "book_updates"
        / f"{checksum_prefix}.csv"
    )
    manifest_path = _manifest_path(
        manifest_dir=Path(manifest_dir),
        vendor=vendor,
        instrument=instrument,
        trade_date=trade_date,
        dataset_type="book_updates",
        checksum_prefix=checksum_prefix,
    )

    copied_raw = _copy_immutable(source_gzip, raw_path, source_checksum)
    bronze_path.parent.mkdir(parents=True, exist_ok=True)
    row_count = 0
    with gzip.open(raw_path, "rt", encoding="utf-8", newline="") as source_file:
        reader = csv.DictReader(source_file)
        if reader.fieldnames != list(TARDIS_INCREMENTAL_BOOK_L2_COLUMNS):
            raise DataValidationError(
                f"Unexpected Tardis L2 schema: {reader.fieldnames}; expected "
                f"{list(TARDIS_INCREMENTAL_BOOK_L2_COLUMNS)}"
            )
        fieldnames = [
            "source_row_number",
            "event_time",
            "receive_time",
            "side",
            "price",
            "quantity",
            "update_type",
            "sequence_id",
            "is_snapshot",
            "source_timestamp",
            "source_local_timestamp",
            "instrument",
            "vendor",
            "vendor_symbol",
            "source_checksum",
        ]
        with bronze_path.open("w", encoding="utf-8", newline="") as bronze_file:
            writer = csv.DictWriter(bronze_file, fieldnames=fieldnames)
            writer.writeheader()
            for source_row_number, row in enumerate(reader, start=1):
                if max_rows is not None and row_count >= max_rows:
                    break
                update_type = "snapshot" if row["is_snapshot"].lower() == "true" else "set"
                writer.writerow(
                    {
                        "source_row_number": source_row_number,
                        "event_time": _epoch_to_utc_iso(row["timestamp"], unit="microseconds"),
                        "receive_time": _epoch_to_utc_iso(
                            row["local_timestamp"], unit="microseconds"
                        ),
                        "side": row["side"].lower(),
                        "price": row["price"],
                        "quantity": row["amount"],
                        "update_type": update_type,
                        "sequence_id": "",
                        "is_snapshot": row["is_snapshot"].lower(),
                        "source_timestamp": row["timestamp"],
                        "source_local_timestamp": row["local_timestamp"],
                        "instrument": instrument,
                        "vendor": row["exchange"],
                        "vendor_symbol": row["symbol"],
                        "source_checksum": source_checksum,
                    }
                )
                row_count += 1

    manifest = {
        "vendor": vendor,
        "vendor_symbol": vendor_symbol,
        "instrument": instrument,
        "dataset_type": "book_updates",
        "trade_date": trade_date,
        "source_schema": list(TARDIS_INCREMENTAL_BOOK_L2_COLUMNS),
        "normalized_schema": fieldnames,
        "timestamp_unit": "microseconds",
        "receive_timestamp_unit": "microseconds",
        "timezone": "UTC",
        "source_gzip_path": str(source_gzip),
        "raw_path": str(raw_path),
        "bronze_path": str(bronze_path),
        "source_checksum_sha256": source_checksum,
        "row_count": row_count,
        "max_rows": max_rows,
        "ingested_at": utc_now_iso(),
    }
    _write_manifest(manifest_path, manifest)

    return VendorIngestionResult(
        vendor=vendor,
        dataset_type="book_updates",
        instrument=instrument,
        vendor_symbol=vendor_symbol,
        trade_date=trade_date,
        raw_path=str(raw_path),
        bronze_path=str(bronze_path),
        manifest_path=str(manifest_path),
        source_checksum=source_checksum,
        vendor_checksum="",
        vendor_checksum_verified=False,
        row_count=row_count,
        copied_raw=copied_raw,
    )


def ingest_tardis_trades_gzip(
    *,
    source_gzip_path: str | Path,
    instrument: str,
    vendor_symbol: str,
    trade_date: str,
    vendor: str = "tardis_binance_spot",
    raw_dir: str | Path = "data/raw",
    bronze_dir: str | Path = "data/bronze",
    manifest_dir: str | Path = "data/manifests",
    max_rows: Optional[int] = None,
) -> VendorIngestionResult:
    """Ingest Tardis normalized trades gzip into bronze trades CSV."""

    source_gzip = Path(source_gzip_path)
    source_checksum = sha256_file(source_gzip)
    checksum_prefix = source_checksum[:16]
    raw_path = (
        Path(raw_dir)
        / _safe_name(vendor)
        / _safe_name(vendor_symbol)
        / _safe_name(trade_date)
        / "trades"
        / f"{checksum_prefix}-{_safe_name(source_gzip.name)}"
    )
    bronze_path = (
        Path(bronze_dir)
        / _safe_name(vendor)
        / _safe_name(instrument)
        / _safe_name(trade_date)
        / "trades"
        / f"{checksum_prefix}.csv"
    )
    manifest_path = _manifest_path(
        manifest_dir=Path(manifest_dir),
        vendor=vendor,
        instrument=instrument,
        trade_date=trade_date,
        dataset_type="trades",
        checksum_prefix=checksum_prefix,
    )

    copied_raw = _copy_immutable(source_gzip, raw_path, source_checksum)
    bronze_path.parent.mkdir(parents=True, exist_ok=True)
    row_count = 0
    with gzip.open(raw_path, "rt", encoding="utf-8", newline="") as source_file:
        reader = csv.DictReader(source_file)
        if reader.fieldnames != list(TARDIS_TRADES_COLUMNS):
            raise DataValidationError(
                f"Unexpected Tardis trades schema: {reader.fieldnames}; expected "
                f"{list(TARDIS_TRADES_COLUMNS)}"
            )
        fieldnames = [
            "source_row_number",
            "event_time",
            "receive_time",
            "price",
            "quantity",
            "side",
            "trade_id",
            "source_trade_id",
            "source_timestamp",
            "source_local_timestamp",
            "instrument",
            "vendor",
            "vendor_symbol",
            "source_checksum",
        ]
        with bronze_path.open("w", encoding="utf-8", newline="") as bronze_file:
            writer = csv.DictWriter(bronze_file, fieldnames=fieldnames)
            writer.writeheader()
            for source_row_number, row in enumerate(reader, start=1):
                if max_rows is not None and row_count >= max_rows:
                    break
                writer.writerow(
                    {
                        "source_row_number": source_row_number,
                        "event_time": _epoch_to_utc_iso(row["timestamp"], unit="microseconds"),
                        "receive_time": _epoch_to_utc_iso(
                            row["local_timestamp"], unit="microseconds"
                        ),
                        "price": row["price"],
                        "quantity": row["amount"],
                        "side": row["side"].lower(),
                        "trade_id": row["id"],
                        "source_trade_id": row["id"],
                        "source_timestamp": row["timestamp"],
                        "source_local_timestamp": row["local_timestamp"],
                        "instrument": instrument,
                        "vendor": row["exchange"],
                        "vendor_symbol": row["symbol"],
                        "source_checksum": source_checksum,
                    }
                )
                row_count += 1

    manifest = {
        "vendor": vendor,
        "vendor_symbol": vendor_symbol,
        "instrument": instrument,
        "dataset_type": "trades",
        "trade_date": trade_date,
        "source_schema": list(TARDIS_TRADES_COLUMNS),
        "normalized_schema": fieldnames,
        "timestamp_unit": "microseconds",
        "receive_timestamp_unit": "microseconds",
        "timezone": "UTC",
        "source_gzip_path": str(source_gzip),
        "raw_path": str(raw_path),
        "bronze_path": str(bronze_path),
        "source_checksum_sha256": source_checksum,
        "row_count": row_count,
        "max_rows": max_rows,
        "ingested_at": utc_now_iso(),
        "side_semantics": "Tardis normalized trade side as aggressor side: buy or sell",
    }
    _write_manifest(manifest_path, manifest)

    return VendorIngestionResult(
        vendor=vendor,
        dataset_type="trades",
        instrument=instrument,
        vendor_symbol=vendor_symbol,
        trade_date=trade_date,
        raw_path=str(raw_path),
        bronze_path=str(bronze_path),
        manifest_path=str(manifest_path),
        source_checksum=source_checksum,
        vendor_checksum="",
        vendor_checksum_verified=False,
        row_count=row_count,
        copied_raw=copied_raw,
    )


def result_to_dict(result: VendorIngestionResult) -> dict[str, Any]:
    return asdict(result)
