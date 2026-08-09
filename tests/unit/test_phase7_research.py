import json
import math
from pathlib import Path

import pandas as pd
import pytest

from microalpha.pipeline.multiday import aggregate_snapshot_hash
from microalpha.pipeline.registry import DEVELOPMENT_DATES
from microalpha.research.phase7 import (
    EXPECTED_SNAPSHOT_HASH,
    FEATURE_VERSION,
    LABEL_VERSION,
    assign_decile_buckets,
    benjamini_hochberg,
    bucket_monotonicity,
    count_valid_rows,
    deterministic_permutation,
    deterministic_results_hash,
    ensure_no_zero_fill,
    next_move_bucket_rows,
    nonoverlap_mask,
    pearson_ic,
    primary_tests,
    spearman_ic,
    split_year,
    summarize_daily_values,
    top_bottom_effect,
    verify_pre_phase7_snapshot,
)


def snapshot_fixture(dates: list[str] | None = None) -> dict:
    included = dates or list(DEVELOPMENT_DATES)
    manifest = {
        "snapshot_version": "pre_phase7_research_snapshot_v1",
        "registry_version": "research_dates_v1",
        "dataset_role": "development",
        "canonical_instrument": "BTC-USDT",
        "vendor": "tardis_binance_spot",
        "cross_day_features": False,
        "cross_day_labels": False,
        "included_dates": included,
        "excluded_dates": [],
        "failed_dates": [],
        "source_checksums": {
            date: {"l2": f"l2-{date}", "trades": f"tr-{date}"}
            for date in included
        },
        "feature_hashes": {date: f"feature-{date}" for date in included},
        "label_hashes": {date: f"label-{date}" for date in included},
        "config_hashes": {"features": "feature-config", "labels": "label-config"},
        "feature_config_hash": "feature-config",
        "label_config_hash": "label-config",
        "feature_version": FEATURE_VERSION,
        "label_version": LABEL_VERSION,
        "repository_commit": "abc123",
    }
    manifest["snapshot_hash"] = aggregate_snapshot_hash(manifest)
    return manifest


def test_primary_matrix_has_exact_30_prespecified_tests() -> None:
    tests = primary_tests()
    assert len(tests) == 30
    assert {test.horizon for test in tests} == {"100ms", "500ms", "1s", "5s", "30s"}
    assert ("qi_1", "1s") in {(test.feature, test.horizon) for test in tests}
    assert ("ofi_1s", "1s") in {(test.feature, test.horizon) for test in tests}


def test_spearman_and_pearson_are_deterministic() -> None:
    x = pd.Series([1, 2, 3, 4])
    y = pd.Series([10, 20, 30, 40])
    assert spearman_ic(x, y) == pytest.approx(1.0)
    assert pearson_ic(x, y) == pytest.approx(1.0)
    assert spearman_ic(x, pd.Series([40, 30, 20, 10])) == pytest.approx(-1.0)


def test_missing_handling_is_pairwise_and_never_zero_filled() -> None:
    feature = pd.Series([1.0, None, 3.0, None])
    label = pd.Series([1.0, 2.0, None, 4.0])
    counts = count_valid_rows(feature, label)
    assert counts["candidate_rows"] == 4
    assert counts["valid_feature_rows"] == 2
    assert counts["valid_label_rows"] == 3
    assert counts["valid_paired_rows"] == 1
    assert counts["paired_coverage"] == pytest.approx(0.25)
    with pytest.raises(ValueError, match="filled with zero"):
        ensure_no_zero_fill(feature, feature.fillna(0.0))


def test_t_stat_sign_consistency_and_sign_test() -> None:
    summary = summarize_daily_values([0.1, 0.2, -0.1, 0.0, math.nan])
    assert summary["valid_days"] == 4
    assert summary["positive_days"] == 2
    assert summary["negative_days"] == 1
    assert summary["zero_days"] == 1
    assert summary["sign_consistency"] == pytest.approx(2 / 3)
    assert math.isfinite(summary["t_stat"])
    assert math.isfinite(summary["raw_p_value"])
    assert math.isfinite(summary["sign_test_p_value"])


def test_benjamini_hochberg_q_values_are_monotone_by_rank() -> None:
    q_values = benjamini_hochberg([0.01, 0.04, 0.03, math.nan])
    assert q_values[:3] == pytest.approx([0.03, 0.04, 0.04])
    assert math.isnan(q_values[3])


def test_bucket_ties_top_bottom_and_monotonicity() -> None:
    feature = pd.Series([1, 1, 1, 2, 2, 3, 3, 4, 4, 5])
    buckets = assign_decile_buckets(feature)
    assert buckets.dropna().tolist() == list(range(1, 11))
    rows = [
        {
            "bucket": bucket,
            "mean_future_return": float(bucket),
            "mean_future_move_bps": float(bucket * 2),
            "observation_count": 1,
        }
        for bucket in range(1, 11)
    ]
    assert top_bottom_effect(rows) == pytest.approx(18.0)
    assert bucket_monotonicity(rows) == pytest.approx(1.0)


def test_next_mid_move_excludes_unavailable_outcomes() -> None:
    rows = next_move_bucket_rows(
        date="2024-01-01",
        feature_name="qi_1",
        feature=pd.Series(range(10)),
        available=pd.Series(
            ["true", "false", "true", "true", "true", "false", "true", "true", "true", "true"]
        ),
        direction=pd.Series(["1", "1", "-1", "", "1", "-1", "-1", "1", "-1", "1"]),
    )
    assert sum(row["count"] for row in rows) == 7
    assert all(row["feature_group"] == "primary" for row in rows)


def test_nonoverlap_mask_uses_deterministic_offset_zero() -> None:
    assert nonoverlap_mask(10, 3).tolist() == [
        True,
        False,
        False,
        True,
        False,
        False,
        True,
        False,
        False,
        True,
    ]


def test_deterministic_permutation_is_stable_and_keyed() -> None:
    series = pd.Series([1, 2, 3, None, 4])
    first = deterministic_permutation(series, seed=7007, key="2024-01-01")
    second = deterministic_permutation(series, seed=7007, key="2024-01-01")
    other = deterministic_permutation(series, seed=7007, key="2024-02-01")
    pd.testing.assert_series_equal(first, second)
    assert first.dropna().tolist() != other.dropna().tolist()
    assert pd.isna(first.iloc[3])


def test_snapshot_verification_accepts_exact_development_manifest() -> None:
    manifest = snapshot_fixture()
    check = verify_pre_phase7_snapshot(manifest, expected_hash=manifest["snapshot_hash"])
    assert check["included_date_count"] == 24
    assert check["first_date"] == "2024-01-01"
    assert check["last_date"] == "2025-12-01"


def test_snapshot_verification_rejects_holdout_dates() -> None:
    manifest = snapshot_fixture(list(DEVELOPMENT_DATES[:-1]) + ["2026-01-01"])
    with pytest.raises(ValueError, match="2026 holdout"):
        verify_pre_phase7_snapshot(manifest, expected_hash=manifest["snapshot_hash"])


def test_snapshot_verification_rejects_nonchronological_development_dates() -> None:
    manifest = snapshot_fixture(list(reversed(DEVELOPMENT_DATES)))
    with pytest.raises(ValueError, match="frozen 2024-2025"):
        verify_pre_phase7_snapshot(manifest, expected_hash=manifest["snapshot_hash"])


def test_committed_snapshot_hash_constant_matches_gate() -> None:
    assert (
        EXPECTED_SNAPSHOT_HASH
        == "0bcdb7eddebbe83458998eff78844471afb78fc66d249a53aeb25667bebd803a"
    )


def test_split_year_allows_only_development_years() -> None:
    assert split_year("2024-01-01") == "2024"
    assert split_year("2025-12-01") == "2025"
    with pytest.raises(ValueError, match="only permits 2024/2025"):
        split_year("2026-01-01")


def test_deterministic_results_hash_excludes_embedded_result_hash(tmp_path: Path) -> None:
    for filename in [
        "primary_ic.csv",
        "daily_ic.csv",
        "nonoverlap_ic.csv",
        "bucket_results.csv",
        "next_move_results.csv",
        "direction_results.csv",
    ]:
        (tmp_path / filename).write_text("a,b\n1,2\n", encoding="utf-8")
    (tmp_path / "phase7_summary.json").write_text(
        json.dumps({"phase7_results_hash": "first", "value": 1}, sort_keys=True),
        encoding="utf-8",
    )
    first = deterministic_results_hash(tmp_path)
    (tmp_path / "phase7_summary.json").write_text(
        json.dumps({"phase7_results_hash": "second", "value": 1}, sort_keys=True),
        encoding="utf-8",
    )
    assert deterministic_results_hash(tmp_path) == first
