"""Multi-day orchestration over a frozen registry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from microalpha.pipeline.day import DayRunConfig, result_to_record_update, run_day
from microalpha.pipeline.registry import load_registry, records_for_role, write_registry
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
) -> dict:
    registry = load_registry(registry_path)
    development = records_for_role(registry["dates"], "development")
    included = [record for record in development if record.get("exclusion_status") == "included"]
    excluded = [record for record in development if record.get("exclusion_status") != "included"]
    payload = {
        "snapshot_version": "pre_phase7_research_snapshot_v1",
        "included_dates": [record["date"] for record in included],
        "excluded_dates": [
            {"date": record["date"], "reason": record.get("exclusion_reason", "")}
            for record in excluded
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
        "feature_version": feature_version,
        "label_version": label_version,
        "repository_commit": repo_commit,
    }
    payload["snapshot_hash"] = hash_config(payload)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload
