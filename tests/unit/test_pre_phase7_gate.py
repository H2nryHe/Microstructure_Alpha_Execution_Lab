import csv
from pathlib import Path

from microalpha.features.engineering import FeatureConfig, build_feature_table
from microalpha.labels.generation import LabelConfig, build_label_table
from microalpha.pipeline.cache import artifact_manifest, cache_is_valid
from microalpha.pipeline.multiday import process_registry
from microalpha.pipeline.registry import (
    empty_registry_record,
    records_for_role,
    write_registry,
)
from microalpha.storage.parquet import csv_to_parquet, parquet_round_trip_matches


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def fixed_fields() -> list[str]:
    return [
        "instrument",
        "observation_time",
        "feature_cutoff_time",
        "is_available",
        "book_event_time",
        "book_observation_time",
        "book_source_row_number",
        "best_bid",
        "bid_sz_1",
        "best_ask",
        "ask_sz_1",
        "mid",
        "spread",
        "latest_trade_event_time",
        "latest_trade_observation_time",
    ]


def state_fields() -> list[str]:
    return [
        "instrument",
        "observation_time",
        "feature_cutoff_time",
        "book_event_time",
        "book_observation_time",
        "book_source_row_number",
        "best_bid",
        "bid_size",
        "best_ask",
        "ask_size",
        "mid",
        "spread",
        "bid_depth",
        "ask_depth",
        "bid_px_1",
        "bid_sz_1",
        "ask_px_1",
        "ask_sz_1",
    ]


def row(time: str, mid: str, source_row: str = "1") -> dict[str, str]:
    best_bid = str(float(mid) - 0.5)
    best_ask = str(float(mid) + 0.5)
    return {
        "instrument": "BTC-USDT",
        "observation_time": time,
        "feature_cutoff_time": time,
        "is_available": "true",
        "book_event_time": time,
        "book_observation_time": time,
        "book_source_row_number": source_row,
        "best_bid": best_bid,
        "bid_size": "10",
        "bid_sz_1": "10",
        "best_ask": best_ask,
        "ask_size": "10",
        "ask_sz_1": "10",
        "mid": mid,
        "spread": "1",
        "bid_depth": "10",
        "ask_depth": "10",
        "bid_px_1": best_bid,
        "ask_px_1": best_ask,
        "latest_trade_event_time": "",
        "latest_trade_observation_time": "",
    }


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def write_empty_trades(path: Path) -> None:
    write_csv(
        path,
        [
            "source_row_number",
            "event_time",
            "receive_time",
            "price",
            "quantity",
            "side",
            "trade_id",
        ],
        [],
    )


def test_parquet_round_trip_preserves_timestamps_and_values(tmp_path: Path) -> None:
    source = tmp_path / "labels.csv"
    parquet = tmp_path / "labels.parquet"
    write_csv(
        source,
        ["feature_cutoff_time", "ret_fwd_100ms", "direction_100ms", "empty_value"],
        [
            {
                "feature_cutoff_time": "2026-01-02T10:00:00.000000+00:00",
                "ret_fwd_100ms": "NaN",
                "direction_100ms": "FLAT",
                "empty_value": "",
            }
        ],
    )

    csv_to_parquet(csv_path=source, parquet_path=parquet)

    assert parquet_round_trip_matches(source, parquet)


def test_day_boundary_feature_isolation_when_cross_day_features_false(tmp_path: Path) -> None:
    day2_time = "2026-01-02T00:00:00.100000+00:00"
    fixed = tmp_path / "fixed_day2.csv"
    states = tmp_path / "states_day2.csv"
    trades = tmp_path / "trades.csv"
    features = tmp_path / "features.csv"
    write_csv(fixed, fixed_fields(), [row(day2_time, "101")])
    write_csv(states, state_fields(), [row(day2_time, "101")])
    write_empty_trades(trades)

    build_feature_table(
        fixed_clock_path=fixed,
        event_state_path=states,
        trades_path=trades,
        output_path=features,
        config=FeatureConfig(
            ofi_windows_ms=(1000,),
            trade_windows_ms=(1000,),
            realized_vol_windows_ms=(1000,),
            momentum_windows_ms=(1000,),
        ),
    )

    feature_row = read_rows(features)[0]
    assert feature_row["mom_1s"] == ""
    assert feature_row["realized_vol_1s"] == ""


def test_day_boundary_label_isolation_when_cross_day_labels_false(tmp_path: Path) -> None:
    fixed = tmp_path / "fixed.csv"
    labels = tmp_path / "labels.csv"
    write_csv(
        fixed,
        fixed_fields(),
        [
            row("2026-01-01T23:59:59.900000+00:00", "100"),
            row("2026-01-02T00:00:00.000000+00:00", "101", "2"),
        ],
    )

    build_label_table(
        research_path=fixed,
        output_path=labels,
        config=LabelConfig(horizons_ms=(100,), cross_session_labels=False),
    )

    label_row = read_rows(labels)[0]
    assert label_row["target_time_100ms"] == "2026-01-02T00:00:00.000000+00:00"
    assert label_row["ret_fwd_100ms"] == ""


def test_dataset_role_isolation() -> None:
    records = [
        empty_registry_record("2024-01-01", "development"),
        empty_registry_record("2026-01-01", "holdout"),
    ]

    assert [record["date"] for record in records_for_role(records, "development")] == [
        "2024-01-01"
    ]


def test_manifest_hash_consistency_and_cache_invalidation(tmp_path: Path) -> None:
    artifact = tmp_path / "features.parquet"
    artifact.write_text("stable artifact\n", encoding="utf-8")
    manifest = artifact_manifest(
        stage="features",
        artifact_path=artifact,
        source_checksums={"l2": "a", "trades": "b"},
        config_hash="cfg1",
        version="microstructure_v1",
    )
    same = artifact_manifest(
        stage="features",
        artifact_path=artifact,
        source_checksums={"l2": "a", "trades": "b"},
        config_hash="cfg1",
        version="microstructure_v1",
    )
    assert manifest["cache_key"] == same["cache_key"]
    assert cache_is_valid(
        manifest,
        artifact_path=artifact,
        source_checksums={"l2": "a", "trades": "b"},
        config_hash="cfg1",
        version="microstructure_v1",
        stage="features",
    )

    invalidators = [
        ({"l2": "changed", "trades": "b"}, "cfg1", "microstructure_v1", "features"),
        ({"l2": "a", "trades": "b"}, "cfg2", "microstructure_v1", "features"),
        ({"l2": "a", "trades": "b"}, "cfg1", "microstructure_v2", "features"),
        ({"l2": "a", "trades": "b"}, "cfg1", "microstructure_v1", "labels"),
    ]
    for source_checksums, config_hash, version, stage in invalidators:
        assert not cache_is_valid(
            manifest,
            artifact_path=artifact,
            source_checksums=source_checksums,
            config_hash=config_hash,
            version=version,
            stage=stage,
        )


def test_partial_failure_records_reason_and_continues(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.yaml"
    registry = {
        "registry_version": "test",
        "dates": [
            empty_registry_record("2024-01-01", "development"),
            empty_registry_record("2024-02-01", "development"),
            empty_registry_record("2026-01-01", "holdout"),
        ],
    }
    write_registry(registry_path, registry)

    def runner(date: str) -> dict:
        if date == "2024-01-01":
            raise RuntimeError("QA failed")
        return {
            "l2_checksum": "l2",
            "trade_checksum": "trades",
            "compressed_file_size": {"l2": 1, "trades": 1},
            "l2_row_count": 2,
            "trade_row_count": 3,
            "qa_status": "PASS",
            "book_replay_status": "PASS",
            "feature_status": "PASS",
            "label_status": "PASS",
            "feature_hash": "fh",
            "label_hash": "lh",
            "runtime_seconds": {"features": 1.0, "labels": 1.0},
            "artifacts": {},
            "cache_keys": {},
        }

    result = process_registry(
        registry_path=registry_path,
        role="development",
        runner=runner,
        stop_on_error=False,
    )

    assert result["processed"] == ["2024-02-01"]
    assert result["failed"][0]["date"] == "2024-01-01"
    updated = read_registry_text(registry_path)
    assert "pipeline failure: RuntimeError: QA failed" in updated
    assert "2026-01-01" in updated


def read_registry_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")
