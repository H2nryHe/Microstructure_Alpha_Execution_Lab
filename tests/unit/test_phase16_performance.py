from __future__ import annotations

import csv
import json
from collections import deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from microalpha.config import load_yaml_config
from microalpha.features.engineering import (
    OFIEvent,
    TradeEvent,
    _ofi_window_features,
    _OFIWindowAccumulator,
    _trade_window_features,
    _TradeWindowAccumulator,
)
from microalpha.research.phase16 import (
    PHASE16_PERFORMANCE_PLAN_HASH,
    assert_no_2026_access,
    phase16_benchmark_artifact_hash,
    phase16_cache_key,
    phase16_results_hash,
    select_phase16_engine,
    validate_phase16_plan,
)
from microalpha.utils.hashing import hash_config


def ts(ms: int) -> datetime:
    return datetime(2024, 7, 1, tzinfo=timezone.utc) + timedelta(milliseconds=ms)


def test_phase16_plan_hash_and_no_2026_access() -> None:
    plan = load_yaml_config("data/manifests/phase16_performance_plan.yaml")
    assert hash_config(plan) == PHASE16_PERFORMANCE_PLAN_HASH
    assert validate_phase16_plan() == PHASE16_PERFORMANCE_PLAN_HASH
    with pytest.raises(ValueError, match="Forbidden 2026"):
        assert_no_2026_access({"date": "2026-01-01"})


def test_window_accumulators_match_frozen_reference() -> None:
    windows = (100, 500)
    trades = [
        TradeEvent(ts(0), ts(0), 1, Decimal("100"), Decimal("1"), "buy"),
        TradeEvent(ts(100), ts(100), 2, Decimal("101"), Decimal("2"), "sell"),
        TradeEvent(ts(250), ts(250), 3, Decimal("102"), Decimal("3"), "buy"),
        TradeEvent(ts(600), ts(600), 4, Decimal("103"), Decimal("4"), "sell"),
    ]
    ofi_events = [
        OFIEvent(ts(0), 1, Decimal("0")),
        OFIEvent(ts(100), 2, Decimal("5")),
        OFIEvent(ts(250), 3, Decimal("-2")),
        OFIEvent(ts(600), 4, Decimal("7")),
    ]
    trade_reference = {window: deque() for window in windows}
    ofi_reference = {window: deque() for window in windows}
    trade_accumulator = _TradeWindowAccumulator(windows)
    ofi_accumulator = _OFIWindowAccumulator(windows)
    trade_index = 0
    ofi_index = 0
    for cutoff in [ts(100), ts(250), ts(600)]:
        while trade_index < len(trades) and trades[trade_index].observation_time <= cutoff:
            trade = trades[trade_index]
            for queue in trade_reference.values():
                queue.append((trade.observation_time, trade))
            trade_accumulator.append(trade)
            trade_index += 1
        while ofi_index < len(ofi_events) and ofi_events[ofi_index].observation_time <= cutoff:
            event = ofi_events[ofi_index]
            for queue in ofi_reference.values():
                queue.append((event.observation_time, event.ofi_event))
            ofi_accumulator.append(event)
            ofi_index += 1
        assert trade_accumulator.features(cutoff) == _trade_window_features(
            trade_reference, cutoff
        )
        assert ofi_accumulator.features(cutoff) == _ofi_window_features(ofi_reference, cutoff)


def test_python_fallback_selected_when_native_unavailable() -> None:
    selection = select_phase16_engine(native_available=False)
    assert selection.engine == "python_reference"
    assert "always available" in selection.reason


def test_cache_identity_invalidates_on_source_or_config_hash_change() -> None:
    baseline = phase16_cache_key(source_hash="source-a", config_hash="config-a")
    assert phase16_cache_key(source_hash="source-a", config_hash="config-a") == baseline
    assert phase16_cache_key(source_hash="source-b", config_hash="config-a") != baseline
    assert phase16_cache_key(source_hash="source-a", config_hash="config-b") != baseline


def test_phase16_compact_hash_generation_is_deterministic(tmp_path: Path) -> None:
    report_dir = tmp_path
    rows = [
        {
            "comparison": "phase5_reference_vs_optimized",
            "status": "PASS",
            "reference_hash": "abc",
            "optimized_hash": "abc",
            "details": "exact",
        }
    ]
    with (report_dir / "equivalence_results.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (report_dir / "phase16_summary.json").write_text(
        json.dumps(
            {
                "phase16_status": "PASS",
                "phase16_results_hash": "",
                "baseline_medians": {"volatile": 1.0},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (report_dir / "PERFORMANCE_ENGINEERING.md").write_text("Phase 16 narrative\n", encoding="utf-8")
    (report_dir / "README.md").write_text("Phase 16 index\n", encoding="utf-8")
    for filename in [
        "baseline_benchmarks.csv",
        "optimized_benchmarks.csv",
        "profile_hotspots.csv",
    ]:
        (report_dir / filename).write_text("stage,runtime_seconds\nx,1\n", encoding="utf-8")
    first = phase16_results_hash(report_dir, {"phase16_status": "PASS"})
    second = phase16_results_hash(report_dir, {"phase16_status": "PASS"})
    assert first == second
    assert phase16_benchmark_artifact_hash(report_dir) == phase16_benchmark_artifact_hash(
        report_dir
    )
