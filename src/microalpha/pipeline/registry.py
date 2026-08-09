"""Dataset registry helpers for the pre-Phase-7 multi-day gate."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from microalpha.config import load_yaml_config

INSTRUMENT = "BTC-USDT"
VENDOR = "tardis_binance_spot"
VENDOR_SYMBOL = "BTCUSDT"
EXCHANGE = "binance"

DEVELOPMENT_DATES = tuple(
    f"{year}-{month:02d}-01" for year in (2024, 2025) for month in range(1, 13)
)
HOLDOUT_DATES = tuple(f"2026-{month:02d}-01" for month in range(1, 9))


def tardis_source_url(date: str, data_type: str, vendor_symbol: str = VENDOR_SYMBOL) -> str:
    year, month, day = date.split("-")
    return (
        f"https://datasets.tardis.dev/v1/{EXCHANGE}/{data_type}/"
        f"{year}/{month}/{day}/{vendor_symbol}.csv.gz"
    )


def empty_registry_record(date: str, dataset_role: str) -> dict[str, Any]:
    return {
        "date": date,
        "instrument": INSTRUMENT,
        "canonical_symbol": INSTRUMENT,
        "vendor": VENDOR,
        "vendor_symbol": VENDOR_SYMBOL,
        "dataset_role": dataset_role,
        "l2_source": tardis_source_url(date, "incremental_book_L2"),
        "trade_source": tardis_source_url(date, "trades"),
        "l2_checksum": "",
        "trade_checksum": "",
        "compressed_file_size": {"l2": None, "trades": None},
        "l2_row_count": None,
        "trade_row_count": None,
        "qa_status": "pending",
        "book_replay_status": "pending",
        "feature_status": "pending",
        "label_status": "pending",
        "feature_hash": "",
        "label_hash": "",
        "exclusion_status": "included",
        "exclusion_reason": "",
        "runtime_seconds": {},
        "artifacts": {},
        "cache_keys": {},
    }


def default_registry_records() -> list[dict[str, Any]]:
    records = [empty_registry_record(date, "development") for date in DEVELOPMENT_DATES]
    records.extend(empty_registry_record(date, "holdout") for date in HOLDOUT_DATES)
    return records


def load_registry(path: str | Path) -> dict[str, Any]:
    registry = load_yaml_config(path)
    if "dates" not in registry or not isinstance(registry["dates"], list):
        raise ValueError(f"Registry must contain a list field named 'dates': {path}")
    return registry


def write_registry(path: str | Path, registry: dict[str, Any]) -> None:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - PyYAML is a project dependency.
        raise RuntimeError("PyYAML is required to write the research date registry") from exc

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        yaml.safe_dump(registry, sort_keys=False, allow_unicode=False),
        encoding="utf-8",
    )


def initialize_registry(path: str | Path) -> dict[str, Any]:
    registry = {
        "registry_version": "research_dates_v1",
        "selection_rule": (
            "Mechanically selected first-of-month BTC-USDT Tardis Binance Spot dates; "
            "development uses 2024-2025, holdout reserves available 2026 dates."
        ),
        "alpha_analysis_performed_before_freeze": False,
        "cross_day_features": False,
        "cross_day_labels": False,
        "engineering_validation_date": "2019-12-01",
        "dates": default_registry_records(),
    }
    write_registry(path, registry)
    return registry


def records_for_role(records: list[dict[str, Any]], role: str) -> list[dict[str, Any]]:
    return [record for record in records if record.get("dataset_role") == role]


def clone_record(record: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(record)


def mark_failure(record: dict[str, Any], stage: str, reason: str) -> dict[str, Any]:
    updated = clone_record(record)
    updated[f"{stage}_status"] = "FAIL"
    updated["exclusion_status"] = "excluded"
    updated["exclusion_reason"] = reason
    return updated
