"""Day-level orchestration for Phases 1-6."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from microalpha.book.replay import read_bronze_book_events
from microalpha.config import load_config_bundle, load_yaml_config
from microalpha.data.qa import assert_can_continue, validate_market_data_csv, write_qa_report
from microalpha.data.vendor_adapters import (
    ingest_tardis_incremental_l2_gzip,
    ingest_tardis_trades_gzip,
)
from microalpha.features.engineering import FeatureConfig, build_feature_table
from microalpha.features.metadata import FEATURE_VERSION
from microalpha.labels.generation import LabelConfig, build_label_table, write_label_summary
from microalpha.pipeline.availability import USER_AGENT
from microalpha.pipeline.cache import artifact_manifest
from microalpha.pipeline.registry import INSTRUMENT, VENDOR_SYMBOL, tardis_source_url
from microalpha.research.dataset import (
    ResearchConfig,
    build_event_state_table,
    build_fixed_clock_table,
)
from microalpha.storage.parquet import csv_to_parquet
from microalpha.utils.hashing import hash_config


@dataclass(frozen=True)
class DayRunConfig:
    work_root: str = "/tmp/microalpha-multiday"
    raw_source_root: str = "/tmp/microalpha-multiday/source"
    config_dir: str = "configs"
    instrument: str = INSTRUMENT
    vendor_symbol: str = VENDOR_SYMBOL
    cross_day_features: bool = False
    cross_day_labels: bool = False
    reuse_cache: bool = True
    stop_on_error: bool = False


def _download_if_needed(url: str, destination: Path) -> tuple[Path, int]:
    if destination.exists():
        return destination, destination.stat().st_size
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"}, method="GET")
    with urlopen(request, timeout=120) as response, destination.open("wb") as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            output.write(chunk)
    return destination, destination.stat().st_size


def _feature_config_from_yaml(path: Path) -> FeatureConfig:
    values = load_yaml_config(path)
    lookbacks = values.get("lookbacks", {})
    return FeatureConfig(
        feature_version=str(values.get("feature_version", FEATURE_VERSION)),
        depth_levels=tuple(int(value) for value in values.get("depth_levels", (5, 10))),
        ofi_windows_ms=tuple(int(value) for value in lookbacks.get("ofi_ms", (1000,))),
        trade_windows_ms=tuple(
            int(value) for value in lookbacks.get("trade_imbalance_ms", (1000,))
        ),
        realized_vol_windows_ms=tuple(
            int(value) for value in lookbacks.get("realized_volatility_ms", (1000,))
        ),
        momentum_windows_ms=tuple(int(value) for value in lookbacks.get("momentum_ms", (1000,))),
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_day(date: str, *, config: DayRunConfig | None = None) -> dict[str, Any]:
    """Run the Phase 1-6 pipeline for one UTC research day.

    The function intentionally processes one day at a time. Cross-day feature and
    label construction defaults are false, so trailing windows and forward labels
    cannot consume adjacent-day artifacts unless explicitly configured later.
    """

    if config is None:
        config = DayRunConfig()
    start = time.perf_counter()
    work_root = Path(config.work_root)
    source_root = Path(config.raw_source_root)
    day_root = work_root / "derived" / f"date={date}"
    raw_dir = work_root / "raw"
    bronze_dir = work_root / "bronze"
    manifest_dir = work_root / "manifests"
    config_dir = Path(config.config_dir)
    config_hash = hash_config(load_config_bundle(config_dir))
    feature_config = _feature_config_from_yaml(config_dir / "features.yaml")
    label_config = LabelConfig.from_mapping(load_yaml_config(config_dir / "labels.yaml"))
    research_config = ResearchConfig(depth=10, sampling_interval_ms=100, max_staleness_ms=1000)

    if config.cross_day_features or config.cross_day_labels:
        raise ValueError("Cross-day features/labels are disabled for the pre-Phase-7 gate")

    l2_url = tardis_source_url(date, "incremental_book_L2", config.vendor_symbol)
    trade_url = tardis_source_url(date, "trades", config.vendor_symbol)
    l2_source, l2_size = _download_if_needed(
        l2_url,
        source_root / date / f"{config.vendor_symbol}_incremental_book_L2.csv.gz",
    )
    trade_source, trade_size = _download_if_needed(
        trade_url,
        source_root / date / f"{config.vendor_symbol}_trades.csv.gz",
    )

    l2_ingest = ingest_tardis_incremental_l2_gzip(
        source_gzip_path=l2_source,
        instrument=config.instrument,
        vendor_symbol=config.vendor_symbol,
        trade_date=date,
        raw_dir=raw_dir,
        bronze_dir=bronze_dir,
        manifest_dir=manifest_dir,
    )
    trade_ingest = ingest_tardis_trades_gzip(
        source_gzip_path=trade_source,
        instrument=config.instrument,
        vendor_symbol=config.vendor_symbol,
        trade_date=date,
        raw_dir=raw_dir,
        bronze_dir=bronze_dir,
        manifest_dir=manifest_dir,
    )

    qa_l2 = validate_market_data_csv(
        l2_ingest.bronze_path,
        dataset_type="book_updates",
        order_timestamp_column="receive_time",
    )
    qa_trades = validate_market_data_csv(
        trade_ingest.bronze_path,
        dataset_type="trades",
        order_timestamp_column="receive_time",
    )
    qa_dir = day_root / "qa"
    write_qa_report(qa_l2, qa_dir / "l2_qa.json")
    write_qa_report(qa_trades, qa_dir / "trades_qa.json")
    assert_can_continue(qa_l2)
    assert_can_continue(qa_trades)

    event_state_path = day_root / "event_states.csv"
    fixed_path = day_root / "research_100ms.csv"
    feature_path = day_root / "features_microstructure_v1.csv"
    label_path = day_root / "labels_microstructure_labels_v1.csv"
    research_parquet = day_root / "research_100ms.parquet"
    feature_parquet = day_root / "features_microstructure_v1.parquet"
    label_parquet = day_root / "labels_microstructure_labels_v1.parquet"

    replay_stats = build_event_state_table(
        book_events=read_bronze_book_events(l2_ingest.bronze_path),
        output_path=event_state_path,
        instrument=config.instrument,
        config=research_config,
    )
    fixed_stats = build_fixed_clock_table(
        event_state_path=event_state_path,
        trades_path=trade_ingest.bronze_path,
        output_path=fixed_path,
        config=research_config,
    )
    feature_stats = build_feature_table(
        fixed_clock_path=fixed_path,
        event_state_path=event_state_path,
        trades_path=trade_ingest.bronze_path,
        output_path=feature_path,
        config=feature_config,
    )
    label_stats = build_label_table(
        research_path=fixed_path,
        output_path=label_path,
        config=label_config,
    )
    write_label_summary(label_stats, day_root / "label_summary.json")

    parquet_hashes = {
        "research": csv_to_parquet(csv_path=fixed_path, parquet_path=research_parquet),
        "features": csv_to_parquet(csv_path=feature_path, parquet_path=feature_parquet),
        "labels": csv_to_parquet(csv_path=label_path, parquet_path=label_parquet),
    }

    source_checksums = {
        "l2": l2_ingest.source_checksum,
        "trades": trade_ingest.source_checksum,
    }
    manifests = {
        "features": artifact_manifest(
            stage="features",
            artifact_path=feature_parquet,
            source_checksums=source_checksums,
            config_hash=config_hash,
            version=feature_config.feature_version,
        ),
        "labels": artifact_manifest(
            stage="labels",
            artifact_path=label_parquet,
            source_checksums=source_checksums,
            config_hash=config_hash,
            version=label_config.label_version,
        ),
    }
    _write_json(day_root / "artifact_manifests.json", manifests)

    result = {
        "date": date,
        "instrument": config.instrument,
        "vendor_symbol": config.vendor_symbol,
        "l2_source": l2_url,
        "trade_source": trade_url,
        "l2_checksum": l2_ingest.source_checksum,
        "trade_checksum": trade_ingest.source_checksum,
        "compressed_file_size": {"l2": l2_size, "trades": trade_size},
        "l2_row_count": l2_ingest.row_count,
        "trade_row_count": trade_ingest.row_count,
        "qa_status": "PASS",
        "book_replay_status": "PASS",
        "feature_status": "PASS",
        "label_status": "PASS",
        "research_rows": fixed_stats.fixed_clock_rows,
        "unavailable_research_rows": fixed_stats.unavailable_stale_rows,
        "feature_rows": feature_stats.total_rows,
        "label_rows": label_stats.total_rows,
        "invalid_crossed_book_states": replay_stats.crossed_or_invalid_states,
        "feature_hash": feature_stats.output_hash,
        "label_hash": label_stats.output_hash,
        "parquet_hashes": parquet_hashes,
        "runtime_seconds": {
            "replay_event_states": replay_stats.processing_time_seconds,
            "research_fixed_clock": fixed_stats.processing_time_seconds,
            "features": feature_stats.processing_time_seconds,
            "labels": label_stats.processing_time_seconds,
            "total": time.perf_counter() - start,
        },
        "artifacts": {
            "event_states_csv": str(event_state_path),
            "research_csv": str(fixed_path),
            "research_parquet": str(research_parquet),
            "features_csv": str(feature_path),
            "features_parquet": str(feature_parquet),
            "labels_csv": str(label_path),
            "labels_parquet": str(label_parquet),
        },
        "cache_keys": {
            "features": manifests["features"]["cache_key"],
            "labels": manifests["labels"]["cache_key"],
        },
    }
    _write_json(day_root / "day_manifest.json", result)
    return result


def result_to_record_update(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in result.items()
        if key
        in {
            "l2_checksum",
            "trade_checksum",
            "compressed_file_size",
            "l2_row_count",
            "trade_row_count",
            "qa_status",
            "book_replay_status",
            "feature_status",
            "label_status",
            "feature_hash",
            "label_hash",
            "runtime_seconds",
            "artifacts",
            "cache_keys",
        }
    }


def dataclass_to_dict(value: Any) -> dict[str, Any]:
    return asdict(value)
