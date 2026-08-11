"""Phase 16 performance engineering helpers."""

from __future__ import annotations

import csv
import hashlib
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from microalpha.config import load_yaml_config
from microalpha.features.engineering import (
    FeatureBuildStats,
    FeatureConfig,
    _feature_fieldnames,
    _mid_series_features,
    _ofi_window_features,
    _state_features,
    _trade_window_features,
    compute_ofi_events,
    read_state_rows,
    read_trade_events,
    summarize_feature_file,
)
from microalpha.research.dataset import dataset_hash, parse_iso_utc
from microalpha.utils.hashing import hash_config

PHASE16_PERFORMANCE_PLAN_HASH = "70fc7a9f1dc3fd80642d0dd83b8d09ba17fd3011be23ba432b92a788a539b350"
FORBIDDEN_HOLDOUT_YEAR = "2026"
PHASE16_VERSION = "phase16_python_window_accumulators_v1"

DETERMINISTIC_RESULT_FILES = [
    "equivalence_results.csv",
    "PERFORMANCE_ENGINEERING.md",
    "README.md",
]
BENCHMARK_ARTIFACT_FILES = [
    "baseline_benchmarks.csv",
    "optimized_benchmarks.csv",
    "profile_hotspots.csv",
]


@dataclass(frozen=True)
class EngineSelection:
    engine: str
    reason: str


def assert_no_2026_access(value: Any, path: str = "phase16") -> None:
    """Reject holdout-year dates/paths in Phase 16 benchmark metadata."""

    if isinstance(value, dict):
        for key, item in value.items():
            assert_no_2026_access(key, f"{path}.{key}")
            assert_no_2026_access(item, f"{path}.{key}")
        return
    if isinstance(value, list | tuple | set):
        for index, item in enumerate(value):
            assert_no_2026_access(item, f"{path}[{index}]")
        return
    if isinstance(value, datetime):
        if value.year == int(FORBIDDEN_HOLDOUT_YEAR):
            raise ValueError(f"Forbidden 2026 holdout access in {path}: {value.isoformat()}")
        return
    if isinstance(value, str) and (
        value.startswith(f"{FORBIDDEN_HOLDOUT_YEAR}-")
        or f"/{FORBIDDEN_HOLDOUT_YEAR}-" in value
        or f"date={FORBIDDEN_HOLDOUT_YEAR}-" in value
    ):
        raise ValueError(f"Forbidden 2026 holdout access in {path}: {value}")


def validate_phase16_plan(path: str | Path = "data/manifests/phase16_performance_plan.yaml") -> str:
    plan = load_yaml_config(path)
    assert_no_2026_access(plan)
    plan_hash = hash_config(plan)
    if plan_hash != PHASE16_PERFORMANCE_PLAN_HASH:
        raise ValueError(f"Phase 16 performance plan hash mismatch: {plan_hash}")
    return plan_hash


def select_phase16_engine(*, native_available: bool = False) -> EngineSelection:
    if native_available:
        return EngineSelection(
            engine="python_reference",
            reason="C++ native path was not introduced because profiling did not justify it.",
        )
    return EngineSelection(
        engine="python_reference",
        reason="Python fallback is the only Phase 16 engine and is always available.",
    )


def phase16_cache_key(
    *,
    source_hash: str,
    config_hash: str,
    optimization_version: str = PHASE16_VERSION,
) -> str:
    return hash_config(
        {
            "artifact": "phase16_cache_identity_v1",
            "source_hash": source_hash,
            "config_hash": config_hash,
            "optimization_version": optimization_version,
        }
    )


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def phase16_benchmark_artifact_hash(output_dir: str | Path) -> str:
    root = Path(output_dir)
    payload: dict[str, Any] = {"artifact": "phase16_benchmark_artifacts_v1"}
    for filename in BENCHMARK_ARTIFACT_FILES:
        payload[filename] = (root / filename).read_text(encoding="utf-8")
    figures = root / "figures"
    if figures.exists():
        payload["figures"] = {
            path.name: file_sha256(path) for path in sorted(figures.glob("*.png"))
        }
    return hash_config(payload)


def phase16_results_hash(output_dir: str | Path, summary_without_hash: dict[str, Any]) -> str:
    root = Path(output_dir)
    payload: dict[str, Any] = {
        "artifact": "phase16_results_v1",
        "summary_without_results_hash": _stable_phase16_summary(summary_without_hash),
    }
    for filename in DETERMINISTIC_RESULT_FILES:
        payload[filename] = (root / filename).read_text(encoding="utf-8")
    return hash_config(payload)


def _stable_phase16_summary(summary: dict[str, Any]) -> dict[str, Any]:
    excluded = {
        "phase16_results_hash",
        "phase16_benchmark_artifact_hash",
        "baseline_medians",
        "optimized_medians",
        "speedups",
        "environment",
    }
    return {key: value for key, value in summary.items() if key not in excluded}


def build_feature_table_reference(
    *,
    fixed_clock_path: str | Path,
    event_state_path: str | Path,
    trades_path: str | Path,
    output_path: str | Path,
    config: FeatureConfig,
) -> FeatureBuildStats:
    """Frozen pre-Phase-16 Phase 5 implementation used for equivalence checks."""

    fixed_rows = read_state_rows(fixed_clock_path)
    state_rows = read_state_rows(event_state_path)
    trades = read_trade_events(trades_path)
    ofi_events = compute_ofi_events(state_rows)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    ofi_index = 0
    trade_index = 0
    ofi_windows = {window: deque() for window in config.ofi_windows_ms}
    trade_windows = {window: deque() for window in config.trade_windows_ms}
    mid_series = _build_mid_series_reference(state_rows)
    fieldnames = _feature_fieldnames(config)

    with output.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in fixed_rows:
            cutoff = parse_iso_utc(row["feature_cutoff_time"])
            while ofi_index < len(ofi_events) and ofi_events[ofi_index].observation_time <= cutoff:
                event = ofi_events[ofi_index]
                for queue in ofi_windows.values():
                    queue.append((event.observation_time, event.ofi_event))
                ofi_index += 1
            while trade_index < len(trades) and trades[trade_index].observation_time <= cutoff:
                trade = trades[trade_index]
                for queue in trade_windows.values():
                    queue.append((trade.observation_time, trade))
                trade_index += 1
            feature_row = {
                "feature_version": config.feature_version,
                "instrument": row["instrument"],
                "observation_time": row["observation_time"],
                "feature_cutoff_time": row["feature_cutoff_time"],
                "is_available": row.get("is_available", ""),
                "book_observation_time": row.get("book_observation_time", ""),
                "book_event_time": row.get("book_event_time", ""),
                "book_source_row_number": row.get("book_source_row_number", ""),
                "latest_trade_observation_time": row.get("latest_trade_observation_time", ""),
                "latest_trade_event_time": row.get("latest_trade_event_time", ""),
            }
            feature_row.update(_state_features(row, config))
            feature_row.update(_ofi_window_features(ofi_windows, cutoff))
            feature_row.update(_trade_window_features(trade_windows, cutoff))
            feature_row.update(_mid_series_features(mid_series, cutoff, config))
            writer.writerow(feature_row)

    return FeatureBuildStats(
        total_rows=len(fixed_rows),
        feature_count=len(fieldnames),
        output_hash=dataset_hash(output),
        processing_time_seconds=0.0,
        feature_version=config.feature_version,
        summary=summarize_feature_file(output),
    )


def _build_mid_series_reference(state_rows: list[dict[str, str]]) -> dict[str, list]:
    from microalpha.features.engineering import _build_mid_series

    return _build_mid_series(state_rows)
