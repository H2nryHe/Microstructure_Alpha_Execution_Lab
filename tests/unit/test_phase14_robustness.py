from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from microalpha.accounting.ledger import Fill
from microalpha.config import load_yaml_config
from microalpha.research.phase14 import (
    MARKET_LATENCIES_MS,
    MODELS,
    ORDER_SIZE_NOTIONAL_USD,
    PASSIVE_DATES,
    PHASE13_COMMIT_SHA,
    PHASE13_EXECUTION_GRID_ARTIFACT_HASH,
    PHASE13_RESULTS_HASH,
    PHASE14_ROBUSTNESS_PLAN_HASH,
    PRIMARY_MARKET_DATES,
    QUEUE_FRACTIONS,
    REPORT_FILES,
    SENSITIVITY_DATES,
    TTL_MS,
    assert_exact_dates,
    breakeven_fee_bps,
    date_level_summary,
    deterministic_sort,
    ensure_negative_days_retained,
    fast_accounting_from_fills,
    fee_quote,
    phase14_results_hash,
    ranking_counts,
    terminal_inventory_stress,
)
from microalpha.utils.hashing import hash_config


def test_exact_six_primary_development_dates() -> None:
    assert_exact_dates(
        PRIMARY_MARKET_DATES,
        [
            "2024-07-01",
            "2024-10-01",
            "2025-01-01",
            "2025-04-01",
            "2025-07-01",
            "2025-10-01",
        ],
        "primary",
    )


def test_exact_three_passive_and_size_dates() -> None:
    assert_exact_dates(PASSIVE_DATES, ["2024-07-01", "2025-01-01", "2025-07-01"], "passive")
    assert_exact_dates(
        SENSITIVITY_DATES,
        ["2024-07-01", "2025-01-01", "2025-07-01"],
        "size",
    )


def test_no_2026_access() -> None:
    with pytest.raises(ValueError, match="forbidden holdout-year"):
        assert_exact_dates(["2026-01-01"], ["2026-01-01"], "bad")


def test_mechanical_model_latency_and_sensitivity_grids_frozen() -> None:
    assert MODELS == ["qi_direct_baseline", "lightgbm_qi_ofi", "lightgbm_extended"]
    assert MARKET_LATENCIES_MS == [0, 100]
    assert ORDER_SIZE_NOTIONAL_USD == [1000.0, 10000.0, 50000.0]
    assert QUEUE_FRACTIONS == [0.5, 1.0]
    assert TTL_MS == [500, 1000, 2000]


def test_phase14_plan_hash_and_frozen_execution_identity() -> None:
    plan = load_yaml_config("data/manifests/phase14_robustness_plan.yaml")
    assert hash_config(plan) == PHASE14_ROBUSTNESS_PLAN_HASH
    assert plan["upstream_artifacts"]["phase13_commit_sha"] == PHASE13_COMMIT_SHA
    assert (
        plan["upstream_artifacts"]["phase13_execution_grid_artifact_hash"]
        == PHASE13_EXECUTION_GRID_ARTIFACT_HASH
    )
    assert plan["upstream_artifacts"]["phase13_results_hash"] == PHASE13_RESULTS_HASH
    assert plan["upstream_artifacts"]["phase10_signal_artifact_hash"]
    assert plan["upstream_artifacts"]["phase11_execution_plan_hash"]
    assert plan["upstream_artifacts"]["phase11_execution_config_hash"]
    assert plan["development_policy"]["models"] == MODELS
    assert plan["primary_market"]["latencies_ms"] == MARKET_LATENCIES_MS
    assert plan["passive_primary"]["latencies_ms"] == MARKET_LATENCIES_MS
    assert plan["development_policy"]["no_model_changes"] is True
    assert plan["development_policy"]["no_threshold_optimization"] is True


def test_fee_overlay_exactness_and_no_cost_double_count() -> None:
    gross = 10.0
    turnover = 100_000.0
    fee = fee_quote(turnover, 0.25)
    net = gross - fee
    assert fee == pytest.approx(2.5)
    assert net == pytest.approx(7.5)
    assert gross == pytest.approx(10.0)


def test_breakeven_calculation() -> None:
    assert breakeven_fee_bps(10.0, 100_000.0) == pytest.approx(1.0)
    assert breakeven_fee_bps(-1.0, 100_000.0) is None


def test_fast_accounting_and_terminal_inventory_mark_shock_formula() -> None:
    fills = [
        Fill(
            fill_id="a",
            order_id="o1",
            fill_time=datetime(2024, 7, 1, tzinfo=timezone.utc),
            side="BUY",
            price=100.0,
            quantity=2.0,
            signed_quantity=2.0,
        )
    ]
    result = fast_accounting_from_fills(fills, terminal_mark_mid=101.0)
    assert result.gross_pnl == pytest.approx(2.0)
    assert result.max_abs_position == pytest.approx(2.0)
    delta, shocked = terminal_inventory_stress(
        terminal_position=2.0,
        terminal_mark_mid=101.0,
        base_equity=result.gross_pnl,
        shock_bps=-10,
    )
    assert delta == pytest.approx(-0.202)
    assert shocked == pytest.approx(1.798)


def test_date_level_aggregation() -> None:
    frame = pd.DataFrame(
        [
            {
                "model": "a",
                "latency_ms": 0,
                "fee_bps": 0.25,
                "gross_pnl": 1.0,
                "net_pnl": 0.5,
                "gross_pnl_bps_of_turnover": 0.1,
            },
            {
                "model": "a",
                "latency_ms": 0,
                "fee_bps": 0.25,
                "gross_pnl": -1.0,
                "net_pnl": -1.5,
                "gross_pnl_bps_of_turnover": -0.2,
            },
        ]
    )
    summary = date_level_summary(frame, ["model", "latency_ms", "fee_bps"])
    row = summary.iloc[0]
    assert row.positive_gross_days == 1
    assert row.negative_gross_days == 1
    assert row.positive_net_days == 1
    assert row.negative_net_days == 1


def test_model_ranking_stability_counts_ties() -> None:
    frame = pd.DataFrame(
        [
            {"date": "d1", "latency_ms": 0, "fee_bps": 0.25, "model": "a", "net_pnl": 1.0},
            {"date": "d1", "latency_ms": 0, "fee_bps": 0.25, "model": "b", "net_pnl": 1.0},
        ]
    )
    counts = ranking_counts(
        frame,
        value_column="net_pnl",
        rank_context_cols=["date", "latency_ms", "fee_bps"],
        metric_label="net_pnl_0_25bps_fee",
    )
    assert counts["metric"].tolist() == ["net_pnl_0_25bps_fee", "net_pnl_0_25bps_fee"]
    assert counts["first_place_count"].sum() == pytest.approx(1.0)


def test_negative_days_retained() -> None:
    ensure_negative_days_retained(pd.DataFrame([{"gross_pnl": -1.0}, {"gross_pnl": 2.0}]))
    with pytest.raises(ValueError, match="no retained negative"):
        ensure_negative_days_retained(pd.DataFrame([{"gross_pnl": 2.0}]))


def test_scenario_isolation_and_deterministic_ordering() -> None:
    frame = pd.DataFrame(
        [
            {"date": "b", "scenario": "size_50000", "value": 1},
            {"date": "a", "scenario": "size_1000", "value": 2},
        ]
    )
    sorted_frame = deterministic_sort(frame, ["date", "scenario"])
    assert sorted_frame["scenario"].tolist() == ["size_1000", "size_50000"]


def test_phase14_report_hash_is_deterministic_when_reports_exist() -> None:
    output_dir = Path("reports/phase14")
    if not (output_dir / "phase14_summary.json").exists():
        pytest.skip("Phase 14 reports are generated artifacts")
    for filename in REPORT_FILES:
        assert (output_dir / filename).exists()
    summary = json.loads((output_dir / "phase14_summary.json").read_text(encoding="utf-8"))
    summary_without_hash = {
        key: value for key, value in summary.items() if key != "phase14_results_hash"
    }
    assert phase14_results_hash(output_dir, summary_without_hash) == summary["phase14_results_hash"]
