"""Multi-day orchestration over a frozen registry."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from microalpha.pipeline.day import DayRunConfig, result_to_record_update, run_day
from microalpha.pipeline.registry import (
    INSTRUMENT,
    VENDOR,
    load_registry,
    records_for_role,
    write_registry,
)
from microalpha.utils.hashing import hash_config


def process_registry(
    *,
    registry_path: str | Path,
    role: str = "development",
    config: DayRunConfig | None = None,
    dates: set[str] | None = None,
    stop_on_error: bool = False,
    runner: Callable[[str], dict] | None = None,
) -> dict:
    if config is None:
        config = DayRunConfig()
    registry = load_registry(registry_path)
    run_one = runner or (lambda date: run_day(date, config=config))
    selected = records_for_role(registry["dates"], role)
    processed = []
    failed = []
    for record in selected:
        date = record["date"]
        if dates is not None and date not in dates:
            continue
        try:
            result = run_one(date)
        except Exception as exc:  # noqa: BLE001 - failure must be recorded.
            record["qa_status"] = "pipeline_failed"
            record["book_replay_status"] = "pipeline_failed"
            record["feature_status"] = "pipeline_failed"
            record["label_status"] = "pipeline_failed"
            record["exclusion_status"] = "excluded"
            record["exclusion_reason"] = f"pipeline failure: {type(exc).__name__}: {exc}"
            failed.append({"date": date, "reason": record["exclusion_reason"]})
            if stop_on_error:
                break
            continue
        record.update(result_to_record_update(result))
        record["exclusion_status"] = "included"
        record["exclusion_reason"] = ""
        processed.append(date)
    write_registry(registry_path, registry)
    return {"processed": processed, "failed": failed, "stop_on_error": stop_on_error}


def build_snapshot_manifest(
    *,
    registry_path: str | Path,
    output_path: str | Path,
    repo_commit: str,
    config_hashes: dict[str, str],
    feature_version: str,
    label_version: str,
    dataset_role: str = "development",
    created_at: str | None = None,
) -> dict:
    registry = load_registry(registry_path)
    selected = records_for_role(registry["dates"], dataset_role)
    included = [record for record in selected if record.get("exclusion_status") == "included"]
    excluded = [record for record in selected if record.get("exclusion_status") == "excluded"]
    failed = [
        record
        for record in selected
        if record.get("exclusion_status") not in {"included", "excluded"}
    ]
    created = created_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload = {
        "snapshot_version": "pre_phase7_research_snapshot_v1",
        "registry_version": registry.get("registry_version", ""),
        "created_at": created,
        "dataset_role": dataset_role,
        "canonical_instrument": INSTRUMENT,
        "vendor": VENDOR,
        "cross_day_features": bool(registry.get("cross_day_features", False)),
        "cross_day_labels": bool(registry.get("cross_day_labels", False)),
        "included_dates": [record["date"] for record in included],
        "excluded_dates": [
            {"date": record["date"], "reason": record.get("exclusion_reason", "")}
            for record in excluded
        ],
        "failed_dates": [
            {
                "date": record["date"],
                "status": record.get("exclusion_status", ""),
                "reason": record.get("exclusion_reason", ""),
            }
            for record in failed
        ],
        "source_checksums": {
            record["date"]: {
                "l2": record.get("l2_checksum", ""),
                "trades": record.get("trade_checksum", ""),
            }
            for record in included
        },
        "feature_hashes": {record["date"]: record.get("feature_hash", "") for record in included},
        "label_hashes": {record["date"]: record.get("label_hash", "") for record in included},
        "config_hashes": config_hashes,
        "feature_config_hash": config_hashes.get("features", ""),
        "label_config_hash": config_hashes.get("labels", ""),
        "feature_version": feature_version,
        "label_version": label_version,
        "repository_commit": repo_commit,
    }
    payload["snapshot_hash"] = aggregate_snapshot_hash(payload)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def aggregate_snapshot_hash(manifest: dict[str, Any]) -> str:
    """Hash logical snapshot identity, excluding local paths and creation time."""

    hash_payload = deepcopy(manifest)
    hash_payload.pop("snapshot_hash", None)
    hash_payload.pop("created_at", None)
    hash_payload.pop("artifact_paths", None)
    for record in hash_payload.get("dates", []):
        if isinstance(record, dict):
            record.pop("artifacts", None)
    return hash_config(hash_payload)
