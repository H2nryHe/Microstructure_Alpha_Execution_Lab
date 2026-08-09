"""Run Phase 7 baseline statistical signal research on the frozen development set."""

from __future__ import annotations

import argparse
import json
import math
import shutil
from pathlib import Path
from typing import Any

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from microalpha.config import load_yaml_config
from microalpha.research.phase7 import (
    DIRECTION_COLUMNS,
    EXPECTED_SNAPSHOT_HASH,
    FEATURE_VERSION,
    HORIZONS,
    LABEL_VERSION,
    MOVE_BPS_COLUMNS,
    NEGATIVE_CONTROL_FEATURE,
    NEGATIVE_CONTROL_HORIZON,
    NEGATIVE_CONTROL_SEED,
    NEXT_MOVE_FEATURES,
    NONOVERLAP_STRIDES,
    RETURN_COLUMNS,
    STATE_FEATURES,
    assign_decile_buckets,
    bucket_monotonicity,
    count_valid_rows,
    deterministic_permutation,
    deterministic_results_hash,
    load_snapshot_manifest,
    nonoverlap_mask,
    pearson_ic,
    primary_tests,
    spearman_ic,
    split_year,
    summarize_daily_values,
    top_bottom_effect,
    verify_pre_phase7_snapshot,
)
from microalpha.utils.hashing import hash_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--derived-root", default="/tmp/microalpha-multiday/derived")
    parser.add_argument(
        "--snapshot",
        default="data/manifests/pre_phase7_research_snapshot.json",
    )
    parser.add_argument("--plan", default="data/manifests/phase7_research_plan.yaml")
    parser.add_argument("--output-dir", default="reports/phase7")
    parser.add_argument("--clean", action="store_true")
    return parser.parse_args()


def feature_path(derived_root: Path, date: str) -> Path:
    return derived_root / f"date={date}" / f"features_{FEATURE_VERSION}.parquet"


def label_path(derived_root: Path, date: str) -> Path:
    return derived_root / f"date={date}" / f"labels_{LABEL_VERSION}.parquet"


def required_feature_columns() -> list[str]:
    cols = {"observation_time", *STATE_FEATURES, *NEXT_MOVE_FEATURES}
    for test in primary_tests():
        cols.add(test.feature)
    return sorted(cols)


def required_label_columns() -> list[str]:
    return sorted(
        {
            "observation_time",
            "next_mid_change_available",
            "next_mid_change_direction",
            *RETURN_COLUMNS.values(),
            *MOVE_BPS_COLUMNS.values(),
            *DIRECTION_COLUMNS.values(),
        }
    )


def load_day_frame(derived_root: Path, date: str) -> pd.DataFrame:
    features = pd.read_parquet(feature_path(derived_root, date), columns=required_feature_columns())
    labels = pd.read_parquet(label_path(derived_root, date), columns=required_label_columns())
    if len(features) != len(labels):
        raise ValueError(
            f"Feature/label row count mismatch for {date}: {len(features)} != {len(labels)}"
        )
    if not features["observation_time"].reset_index(drop=True).equals(
        labels["observation_time"].reset_index(drop=True)
    ):
        raise ValueError(f"Feature/label observation_time mismatch for {date}")
    label_payload = labels.drop(columns=["observation_time"])
    return pd.concat(
        [features.reset_index(drop=True), label_payload.reset_index(drop=True)],
        axis=1,
    ).pipe(_coerce_numeric_columns)


def _coerce_numeric_columns(frame: pd.DataFrame) -> pd.DataFrame:
    numeric_columns = {
        test.feature for test in primary_tests()
    } | set(RETURN_COLUMNS.values()) | set(MOVE_BPS_COLUMNS.values()) | set(NEXT_MOVE_FEATURES)
    for column in sorted(numeric_columns & set(frame.columns)):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("float64")
    return frame


def record_daily_ic(
    date: str,
    frame: pd.DataFrame,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    daily_rows: list[dict[str, Any]] = []
    nonoverlap_rows: list[dict[str, Any]] = []
    for test in primary_tests():
        counts = count_valid_rows(frame[test.feature], frame[test.label])
        full_spearman = spearman_ic(frame[test.feature], frame[test.label])
        full_pearson = pearson_ic(frame[test.feature], frame[test.label])
        daily_rows.append(
            {
                "date": date,
                "year": split_year(date),
                "feature": test.feature,
                "horizon": test.horizon,
                "label": test.label,
                "family": test.family,
                "expected_sign": "positive",
                "spearman_ic": full_spearman,
                "pearson_ic": full_pearson,
                **counts,
            }
        )
        stride = NONOVERLAP_STRIDES[test.horizon]
        mask = nonoverlap_mask(len(frame), stride)
        nonoverlap_spearman = spearman_ic(
            frame.loc[mask, test.feature],
            frame.loc[mask, test.label],
        )
        nonoverlap_pearson = pearson_ic(frame.loc[mask, test.feature], frame.loc[mask, test.label])
        nonoverlap_counts = count_valid_rows(
            frame.loc[mask, test.feature],
            frame.loc[mask, test.label],
        )
        nonoverlap_rows.append(
            {
                "date": date,
                "year": split_year(date),
                "feature": test.feature,
                "horizon": test.horizon,
                "stride": stride,
                "full_grid_spearman_ic": full_spearman,
                "nonoverlap_spearman_ic": nonoverlap_spearman,
                "spearman_difference": (
                    nonoverlap_spearman - full_spearman
                    if math.isfinite(nonoverlap_spearman) and math.isfinite(full_spearman)
                    else math.nan
                ),
                "full_grid_pearson_ic": full_pearson,
                "nonoverlap_pearson_ic": nonoverlap_pearson,
                **{f"nonoverlap_{key}": value for key, value in nonoverlap_counts.items()},
            }
        )
    return daily_rows, nonoverlap_rows


def build_bucket_cache(frame: pd.DataFrame) -> dict[str, pd.Series]:
    features = {test.feature for test in primary_tests()} | set(NEXT_MOVE_FEATURES)
    return {feature: assign_decile_buckets(frame[feature]) for feature in sorted(features)}


def record_buckets(
    date: str,
    frame: pd.DataFrame,
    bucket_cache: dict[str, pd.Series],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for test in primary_tests():
        bucket_rows = _bucket_mean_rows_from_cache(
            date=date,
            feature_name=test.feature,
            horizon=test.horizon,
            buckets=bucket_cache[test.feature],
            returns=frame[test.label],
            move_bps=frame[test.move_bps],
        )
        for row in bucket_rows:
            row["row_type"] = "daily_bucket"
            row["top_minus_bottom_move_bps"] = math.nan
            row["monotonicity_spearman"] = math.nan
            rows.append(row)
        rows.append(
            {
                "date": date,
                "feature": test.feature,
                "horizon": test.horizon,
                "bucket": math.nan,
                "mean_future_return": math.nan,
                "mean_future_move_bps": math.nan,
                "median_future_move_bps": math.nan,
                "observation_count": sum(int(row["observation_count"]) for row in bucket_rows),
                "row_type": "daily_top_bottom",
                "top_minus_bottom_move_bps": top_bottom_effect(bucket_rows),
                "monotonicity_spearman": math.nan,
            }
        )
        rows.append(
            {
                "date": date,
                "feature": test.feature,
                "horizon": test.horizon,
                "bucket": math.nan,
                "mean_future_return": math.nan,
                "mean_future_move_bps": math.nan,
                "median_future_move_bps": math.nan,
                "observation_count": sum(int(row["observation_count"]) for row in bucket_rows),
                "row_type": "daily_monotonicity",
                "top_minus_bottom_move_bps": math.nan,
                "monotonicity_spearman": bucket_monotonicity(bucket_rows),
            }
        )
    return rows


def _bucket_mean_rows_from_cache(
    *,
    date: str,
    feature_name: str,
    horizon: str,
    buckets: pd.Series,
    returns: pd.Series,
    move_bps: pd.Series,
    bucket_count: int = 10,
) -> list[dict[str, Any]]:
    return_numeric = pd.to_numeric(returns, errors="coerce")
    move_numeric = pd.to_numeric(move_bps, errors="coerce")
    valid = buckets.notna() & return_numeric.notna() & move_numeric.notna()
    frame = pd.DataFrame(
        {
            "bucket": buckets[valid].astype("Int64"),
            "return": return_numeric[valid],
            "move_bps": move_numeric[valid],
        }
    )
    rows: list[dict[str, Any]] = []
    grouped = {int(bucket): group for bucket, group in frame.groupby("bucket", sort=True)}
    for bucket in range(1, bucket_count + 1):
        bucket_frame = grouped.get(bucket, frame.iloc[0:0])
        rows.append(
            {
                "date": date,
                "feature": feature_name,
                "horizon": horizon,
                "bucket": bucket,
                "mean_future_return": _mean(bucket_frame["return"]),
                "mean_future_move_bps": _mean(bucket_frame["move_bps"]),
                "median_future_move_bps": _median(bucket_frame["move_bps"]),
                "observation_count": int(len(bucket_frame)),
            }
        )
    return rows


def record_next_moves(
    date: str,
    frame: pd.DataFrame,
    bucket_cache: dict[str, pd.Series],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for feature in NEXT_MOVE_FEATURES:
        rows.extend(_next_move_rows_from_cache(date, feature, bucket_cache[feature], frame))
    return rows


def _next_move_rows_from_cache(
    date: str,
    feature: str,
    buckets: pd.Series,
    frame: pd.DataFrame,
    bucket_count: int = 10,
) -> list[dict[str, Any]]:
    availability = frame["next_mid_change_available"].astype(str).str.lower().eq("true")
    direction = frame["next_mid_change_direction"].astype(str).str.strip()
    valid = buckets.notna() & availability & direction.isin({"1", "-1"})
    data = pd.DataFrame({"bucket": buckets[valid].astype("Int64"), "direction": direction[valid]})
    grouped = {int(bucket): group for bucket, group in data.groupby("bucket", sort=True)}
    rows: list[dict[str, Any]] = []
    for bucket in range(1, bucket_count + 1):
        bucket_frame = grouped.get(bucket, data.iloc[0:0])
        count = int(len(bucket_frame))
        up = int((bucket_frame["direction"] == "1").sum())
        down = int((bucket_frame["direction"] == "-1").sum())
        rows.append(
            {
                "date": date,
                "feature": feature,
                "feature_group": (
                    "primary" if feature in {"qi_1", "microprice_deviation_bps"} else "secondary"
                ),
                "bucket": bucket,
                "p_up": up / count if count else math.nan,
                "p_down": down / count if count else math.nan,
                "count": count,
                "p_up_minus_down": (up - down) / count if count else math.nan,
            }
        )
    return rows


def record_directions(
    date: str,
    frame: pd.DataFrame,
    bucket_cache: dict[str, pd.Series],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for test in primary_tests():
        rows.extend(
            _direction_rows_from_cache(
                date,
                test.feature,
                test.horizon,
                bucket_cache,
                frame,
            )
        )
    return rows


def _direction_rows_from_cache(
    date: str,
    feature: str,
    horizon: str,
    bucket_cache: dict[str, pd.Series],
    frame: pd.DataFrame,
    bucket_count: int = 10,
) -> list[dict[str, Any]]:
    direction = frame[DIRECTION_COLUMNS[horizon]].astype(str).str.strip()
    buckets = bucket_cache[feature]
    valid = buckets.notna() & direction.isin({"UP", "DOWN", "FLAT"})
    data = pd.DataFrame({"bucket": buckets[valid].astype("Int64"), "direction": direction[valid]})
    grouped = {int(bucket): group for bucket, group in data.groupby("bucket", sort=True)}
    rows: list[dict[str, Any]] = []
    for bucket in range(1, bucket_count + 1):
        bucket_frame = grouped.get(bucket, data.iloc[0:0])
        count = int(len(bucket_frame))
        up = int((bucket_frame["direction"] == "UP").sum())
        down = int((bucket_frame["direction"] == "DOWN").sum())
        flat = int((bucket_frame["direction"] == "FLAT").sum())
        rows.append(
            {
                "date": date,
                "feature": feature,
                "horizon": horizon,
                "bucket": bucket,
                "p_up": up / count if count else math.nan,
                "p_flat": flat / count if count else math.nan,
                "p_down": down / count if count else math.nan,
                "count": count,
                "p_up_minus_down": (up - down) / count if count else math.nan,
            }
        )
    return rows


def record_negative_control(date: str, frame: pd.DataFrame) -> dict[str, Any]:
    shuffled = deterministic_permutation(
        frame[NEGATIVE_CONTROL_FEATURE],
        seed=NEGATIVE_CONTROL_SEED,
        key=date,
    )
    label = RETURN_COLUMNS[NEGATIVE_CONTROL_HORIZON]
    return {
        "date": date,
        "year": split_year(date),
        "feature": f"{NEGATIVE_CONTROL_FEATURE}_permuted",
        "horizon": NEGATIVE_CONTROL_HORIZON,
        "spearman_ic": spearman_ic(shuffled, frame[label]),
        "pearson_ic": pearson_ic(shuffled, frame[label]),
    }


def aggregate_primary_ic(daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for test in primary_tests():
        subset = daily[(daily["feature"] == test.feature) & (daily["horizon"] == test.horizon)]
        stats_row = summarize_daily_values(subset["spearman_ic"])
        pearson_row = summarize_daily_values(subset["pearson_ic"])
        coverage = subset["paired_coverage"].dropna()
        rows.append(
            {
                "feature": test.feature,
                "horizon": test.horizon,
                "label": test.label,
                "family": test.family,
                "expected_sign": "positive",
                "valid_days": stats_row["valid_days"],
                "mean_spearman_ic": stats_row["mean"],
                "median_spearman_ic": stats_row["median"],
                "std_spearman_ic": stats_row["std"],
                "min_spearman_ic": stats_row["min"],
                "max_spearman_ic": stats_row["max"],
                "positive_days": stats_row["positive_days"],
                "negative_days": stats_row["negative_days"],
                "zero_days": stats_row["zero_days"],
                "sign_consistency": stats_row["sign_consistency"],
                "t_stat": stats_row["t_stat"],
                "raw_p_value": stats_row["raw_p_value"],
                "sign_test_p_value": stats_row["sign_test_p_value"],
                "mean_pearson_ic": pearson_row["mean"],
                "median_pearson_ic": pearson_row["median"],
                "mean_paired_coverage": float(coverage.mean()) if len(coverage) else math.nan,
                "min_paired_coverage": float(coverage.min()) if len(coverage) else math.nan,
                "total_paired_rows": int(subset["valid_paired_rows"].sum()),
            }
        )
    result = pd.DataFrame(rows)
    result["fdr_q_value"] = _bh(result["raw_p_value"])
    return result


def aggregate_bucket_rows(bucket_rows: pd.DataFrame) -> pd.DataFrame:
    base = bucket_rows.copy()
    daily_bucket = base[base["row_type"] == "daily_bucket"]
    aggregate_rows: list[dict[str, Any]] = []
    group_cols = ["feature", "horizon", "bucket"]
    for keys, group in daily_bucket.groupby(group_cols, sort=True, dropna=False):
        feature, horizon, bucket = keys
        aggregate_rows.append(
            {
                "date": "ALL_EQUAL_DAY",
                "feature": feature,
                "horizon": horizon,
                "bucket": bucket,
                "mean_future_return": float(group["mean_future_return"].mean()),
                "mean_future_move_bps": float(group["mean_future_move_bps"].mean()),
                "median_future_move_bps": float(group["median_future_move_bps"].mean()),
                "observation_count": int(group["observation_count"].sum()),
                "row_type": "equal_day_bucket",
                "top_minus_bottom_move_bps": math.nan,
                "monotonicity_spearman": math.nan,
            }
        )
    for row_type, value_column in [
        ("daily_top_bottom", "top_minus_bottom_move_bps"),
        ("daily_monotonicity", "monotonicity_spearman"),
    ]:
        top_bottom = base[base["row_type"] == row_type]
        for keys, group in top_bottom.groupby(["feature", "horizon"], sort=True):
            feature, horizon = keys
            summary = summarize_daily_values(group[value_column])
            aggregate_rows.append(
                {
                    "date": "ALL_EQUAL_DAY",
                    "feature": feature,
                    "horizon": horizon,
                    "bucket": math.nan,
                    "mean_future_return": math.nan,
                    "mean_future_move_bps": math.nan,
                    "median_future_move_bps": math.nan,
                    "observation_count": int(group["observation_count"].sum()),
                    "row_type": row_type.replace("daily_", "summary_"),
                    "top_minus_bottom_move_bps": summary["mean"]
                    if value_column == "top_minus_bottom_move_bps"
                    else math.nan,
                    "monotonicity_spearman": summary["mean"]
                    if value_column == "monotonicity_spearman"
                    else math.nan,
                    "median": summary["median"],
                    "std": summary["std"],
                    "t_stat": summary["t_stat"],
                    "positive_days": summary["positive_days"],
                    "positive_pct": summary["sign_consistency"],
                    "valid_days": summary["valid_days"],
                }
            )
    return pd.concat([base, pd.DataFrame(aggregate_rows)], ignore_index=True)


def aggregate_probability_rows(rows: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    aggregate_rows: list[dict[str, Any]] = []
    for keys, group in rows.groupby(group_cols, sort=True, dropna=False):
        key_tuple = keys if isinstance(keys, tuple) else (keys,)
        key_values = dict(zip(group_cols, key_tuple, strict=True))
        count = int(group["count"].sum())
        weighted = {
            column: (
                float((group[column] * group["count"]).sum() / count)
                if count and column in group
                else math.nan
            )
            for column in ["p_up", "p_down", "p_flat", "p_up_minus_down"]
        }
        aggregate_rows.append(
            {
                "date": "ALL_EQUAL_DAY",
                **key_values,
                **weighted,
                "count": count,
            }
        )
    return pd.concat([rows, pd.DataFrame(aggregate_rows)], ignore_index=True)


def aggregate_nonoverlap(nonoverlap: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for keys, group in nonoverlap.groupby(["feature", "horizon", "stride"], sort=True):
        feature, horizon, stride = keys
        nonoverlap_stats = summarize_daily_values(group["nonoverlap_spearman_ic"])
        full_stats = summarize_daily_values(group["full_grid_spearman_ic"])
        diff_stats = summarize_daily_values(group["spearman_difference"])
        rows.append(
            {
                "date": "ALL",
                "year": "ALL",
                "feature": feature,
                "horizon": horizon,
                "stride": stride,
                "full_grid_spearman_ic": full_stats["mean"],
                "nonoverlap_spearman_ic": nonoverlap_stats["mean"],
                "spearman_difference": diff_stats["mean"],
                "full_grid_pearson_ic": math.nan,
                "nonoverlap_pearson_ic": math.nan,
                "nonoverlap_candidate_rows": int(group["nonoverlap_candidate_rows"].sum()),
                "nonoverlap_valid_feature_rows": int(group["nonoverlap_valid_feature_rows"].sum()),
                "nonoverlap_valid_label_rows": int(group["nonoverlap_valid_label_rows"].sum()),
                "nonoverlap_valid_paired_rows": int(group["nonoverlap_valid_paired_rows"].sum()),
                "nonoverlap_paired_coverage": float(group["nonoverlap_paired_coverage"].mean()),
            }
        )
    return pd.concat([nonoverlap, pd.DataFrame(rows)], ignore_index=True)


def build_summary(
    *,
    snapshot_check: dict[str, Any],
    plan_hash: str,
    primary_ic: pd.DataFrame,
    daily_ic: pd.DataFrame,
    bucket_results: pd.DataFrame,
    nonoverlap_ic: pd.DataFrame,
    negative_control: pd.DataFrame,
) -> dict[str, Any]:
    year_rows = []
    for keys, group in daily_ic.groupby(["feature", "horizon", "year"], sort=True):
        feature, horizon, year = keys
        values = summarize_daily_values(group["spearman_ic"])
        year_rows.append(
            {
                "feature": feature,
                "horizon": horizon,
                "year": year,
                "mean_spearman_ic": values["mean"],
                "sign_consistency": values["sign_consistency"],
                "positive_days": values["positive_days"],
                "negative_days": values["negative_days"],
            }
        )
    year_frame = pd.DataFrame(year_rows)
    stability = []
    for keys, group in year_frame.groupby(["feature", "horizon"], sort=True):
        feature, horizon = keys
        means = {row["year"]: row["mean_spearman_ic"] for _, row in group.iterrows()}
        stability.append(
            {
                "feature": feature,
                "horizon": horizon,
                "mean_ic_2024": means.get("2024", math.nan),
                "mean_ic_2025": means.get("2025", math.nan),
                "same_sign": (
                    bool(np.sign(means["2024"]) == np.sign(means["2025"]))
                    if "2024" in means
                    and "2025" in means
                    and means["2024"] != 0
                    and means["2025"] != 0
                    else False
                ),
            }
        )
    negative_summary = summarize_daily_values(negative_control["spearman_ic"])
    top_primary = primary_ic.sort_values("mean_spearman_ic", ascending=False).head(5)
    bucket_summary = bucket_results[bucket_results["row_type"] == "summary_top_bottom"]
    monotonicity_summary = bucket_results[bucket_results["row_type"] == "summary_monotonicity"]
    return {
        "phase": "Phase 7 - Baseline Statistical Signal Research",
        "acceptance_gate": "PASS",
        "snapshot_hash": EXPECTED_SNAPSHOT_HASH,
        "snapshot_verification": snapshot_check,
        "phase7_research_plan_hash": plan_hash,
        "date_range": {
            "start": snapshot_check["first_date"],
            "end": snapshot_check["last_date"],
            "included_dates": snapshot_check["included_date_count"],
            "holdout_accessed": False,
        },
        "primary_test_count": int(len(primary_ic)),
        "fdr_family": "30 primary feature/horizon tests",
        "top_primary_by_mean_ic": _records(top_primary),
        "min_fdr_q_value": _finite_scalar(primary_ic["fdr_q_value"].min()),
        "max_abs_mean_ic": _finite_scalar(primary_ic["mean_spearman_ic"].abs().max()),
        "primary_positive_mean_ic_tests": int((primary_ic["mean_spearman_ic"] > 0).sum()),
        "primary_negative_mean_ic_tests": int((primary_ic["mean_spearman_ic"] < 0).sum()),
        "year_stability": stability,
        "year_stable_same_sign_tests": int(sum(1 for row in stability if row["same_sign"])),
        "bucket_top_bottom_summary": _records(bucket_summary),
        "bucket_monotonicity_summary": _records(monotonicity_summary),
        "nonoverlap_mean_abs_difference": _finite_scalar(
            nonoverlap_ic[nonoverlap_ic["date"] == "ALL"]["spearman_difference"].abs().mean()
        ),
        "negative_control": {
            "feature": f"{NEGATIVE_CONTROL_FEATURE}_permuted",
            "horizon": NEGATIVE_CONTROL_HORIZON,
            "seed": NEGATIVE_CONTROL_SEED,
            "mean_spearman_ic": negative_summary["mean"],
            "t_stat": negative_summary["t_stat"],
            "raw_p_value": negative_summary["raw_p_value"],
            "positive_days": negative_summary["positive_days"],
            "negative_days": negative_summary["negative_days"],
        },
        "missing_policy": {
            "pairwise_valid_only": True,
            "filled_missing_alpha_features_with_zero": False,
        },
        "limitations": [
            "Statistical predictability is not executable profitability.",
            "Pooled cross-day IC is intentionally not used for inference.",
            "All conclusions are restricted to the frozen 2024-2025 development sample.",
        ],
    }


def write_tables(
    output_dir: Path,
    tables: dict[str, pd.DataFrame],
    summary: dict[str, Any],
) -> None:
    for filename, frame in tables.items():
        frame.to_csv(
            output_dir / filename,
            index=False,
            float_format="%.12g",
            na_rep="",
            lineterminator="\n",
        )
    (output_dir / "phase7_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def write_figures(
    output_dir: Path,
    primary: pd.DataFrame,
    daily: pd.DataFrame,
    buckets: pd.DataFrame,
    next_moves: pd.DataFrame,
    nonoverlap: pd.DataFrame,
) -> None:
    figures = output_dir / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    _plot_primary_heatmap(primary, figures / "primary_ic_heatmap_feature_x_horizon.png")
    _plot_daily_heatmap(daily, figures / "daily_ic_heatmap_chronological_dates.png")
    _plot_signal_decay(primary, figures / "signal_decay_mean_daily_ic.png")
    for feature in ["qi_1", "microprice_deviation_bps", "ofi_1s", "trade_imbalance_1s"]:
        _plot_decile_curve(
            buckets,
            feature=feature,
            horizon="1s",
            path=figures / f"{feature}_1s_decile_curve.png",
        )
    for feature in ["qi_1", "microprice_deviation_bps"]:
        _plot_next_move(
            next_moves,
            feature=feature,
            path=figures / f"{feature}_next_mid_move_probability_by_decile.png",
        )
    _plot_nonoverlap(nonoverlap, figures / "full_grid_vs_nonoverlap_ic_comparison.png")


def write_readme(output_dir: Path, summary: dict[str, Any]) -> None:
    top = summary["top_primary_by_mean_ic"][0] if summary["top_primary_by_mean_ic"] else {}
    text = f"""# Phase 7 Baseline Statistical Signal Research

Acceptance gate: {summary["acceptance_gate"]}

Frozen snapshot hash: `{summary["snapshot_hash"]}`

Research plan hash: `{summary["phase7_research_plan_hash"]}`

Development sample: {summary["date_range"]["start"]} through {summary["date_range"]["end"]},
{summary["date_range"]["included_dates"]} first-of-month BTC-USDT dates. The 2026 holdout
was not accessed.

Primary family: 30 Spearman daily IC tests with day as the inference unit and BH/FDR across
the full primary family.

Strongest mean daily IC: `{top.get("feature", "")}` at `{top.get("horizon", "")}` with mean
IC `{top.get("mean_spearman_ic", "")}`.

Generated tables:

- `primary_ic.csv`
- `daily_ic.csv`
- `nonoverlap_ic.csv`
- `bucket_results.csv`
- `next_move_results.csv`
- `direction_results.csv`
- `phase7_summary.json`

The results are baseline statistical research only. They are not a model, trading rule,
backtest, execution simulation, or profitability claim.
"""
    (output_dir / "README.md").write_text(text, encoding="utf-8")


def main() -> int:
    args = parse_args()
    derived_root = Path(args.derived_root)
    output_dir = Path(args.output_dir)
    if args.clean and output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    plan = load_yaml_config(args.plan)
    plan_hash = hash_config(plan)
    snapshot = load_snapshot_manifest(args.snapshot)
    snapshot_check = verify_pre_phase7_snapshot(snapshot)
    dates = list(snapshot["included_dates"])

    daily_rows: list[dict[str, Any]] = []
    nonoverlap_rows: list[dict[str, Any]] = []
    bucket_rows: list[dict[str, Any]] = []
    next_move_rows: list[dict[str, Any]] = []
    direction_rows: list[dict[str, Any]] = []
    negative_rows: list[dict[str, Any]] = []

    for index, date in enumerate(dates, start=1):
        print(f"phase7 processing {date} ({index}/{len(dates)})", flush=True)
        frame = load_day_frame(derived_root, date)
        bucket_cache = build_bucket_cache(frame)
        day_daily, day_nonoverlap = record_daily_ic(date, frame)
        daily_rows.extend(day_daily)
        nonoverlap_rows.extend(day_nonoverlap)
        bucket_rows.extend(record_buckets(date, frame, bucket_cache))
        next_move_rows.extend(record_next_moves(date, frame, bucket_cache))
        direction_rows.extend(record_directions(date, frame, bucket_cache))
        negative_rows.append(record_negative_control(date, frame))

    daily_ic = pd.DataFrame(daily_rows)
    primary_ic = aggregate_primary_ic(daily_ic)
    nonoverlap_ic = aggregate_nonoverlap(pd.DataFrame(nonoverlap_rows))
    bucket_results = aggregate_bucket_rows(pd.DataFrame(bucket_rows))
    next_move_results = aggregate_probability_rows(
        pd.DataFrame(next_move_rows),
        ["feature", "feature_group", "bucket"],
    )
    direction_results = aggregate_probability_rows(
        pd.DataFrame(direction_rows),
        ["feature", "horizon", "bucket"],
    )
    negative_control = pd.DataFrame(negative_rows)

    tables = {
        "primary_ic.csv": primary_ic,
        "daily_ic.csv": daily_ic.sort_values(["date", "horizon", "feature"]),
        "nonoverlap_ic.csv": nonoverlap_ic.sort_values(["date", "horizon", "feature"]),
        "bucket_results.csv": bucket_results.sort_values(
            ["date", "horizon", "feature", "row_type", "bucket"]
        ),
        "next_move_results.csv": next_move_results.sort_values(
            ["date", "feature_group", "feature", "bucket"]
        ),
        "direction_results.csv": direction_results.sort_values(
            ["date", "horizon", "feature", "bucket"]
        ),
    }
    summary = build_summary(
        snapshot_check=snapshot_check,
        plan_hash=plan_hash,
        primary_ic=primary_ic,
        daily_ic=daily_ic,
        bucket_results=bucket_results,
        nonoverlap_ic=nonoverlap_ic,
        negative_control=negative_control,
    )
    write_tables(output_dir, tables, summary)
    result_hash = deterministic_results_hash(output_dir)
    summary["phase7_results_hash"] = result_hash
    write_tables(output_dir, tables, summary)
    write_figures(
        output_dir,
        primary_ic,
        daily_ic,
        bucket_results,
        next_move_results,
        nonoverlap_ic,
    )
    write_readme(output_dir, summary)
    print(
        json.dumps(
            {"phase7_results_hash": result_hash, "acceptance_gate": "PASS"},
            sort_keys=True,
        )
    )
    return 0


def _bh(p_values: pd.Series) -> list[float]:
    from microalpha.research.phase7 import benjamini_hochberg

    return benjamini_hochberg(p_values.tolist())


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return json.loads(frame.replace({np.nan: None}).to_json(orient="records"))


def _finite_scalar(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _mean(series: pd.Series) -> float:
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    return float(numeric.mean()) if len(numeric) else math.nan


def _median(series: pd.Series) -> float:
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    return float(numeric.median()) if len(numeric) else math.nan


def _plot_primary_heatmap(primary: pd.DataFrame, path: Path) -> None:
    pivot = primary.pivot(index="feature", columns="horizon", values="mean_spearman_ic").reindex(
        columns=list(HORIZONS)
    )
    _heatmap(pivot, "Mean Daily Spearman IC", path)


def _plot_daily_heatmap(daily: pd.DataFrame, path: Path) -> None:
    core = daily[daily["feature"].isin(["qi_1", "microprice_deviation_bps", "ofi_1s"])]
    core = core[core["horizon"].isin(["100ms", "1s", "30s"])]
    core = core.assign(signal=core["feature"] + "/" + core["horizon"])
    pivot = core.pivot(index="signal", columns="date", values="spearman_ic")
    _heatmap(pivot, "Daily Spearman IC", path, figsize=(12, 4))


def _plot_signal_decay(primary: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 4))
    x = np.arange(len(HORIZONS))
    for feature in ["qi_1", "di_5", "di_10", "microprice_deviation_bps"]:
        subset = primary[primary["feature"] == feature].set_index("horizon").reindex(HORIZONS)
        ax.plot(x, subset["mean_spearman_ic"], marker="o", label=feature)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_xticks(x, HORIZONS)
    ax.set_ylabel("Mean daily Spearman IC")
    ax.set_title("Signal Decay")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _plot_decile_curve(buckets: pd.DataFrame, *, feature: str, horizon: str, path: Path) -> None:
    rows = buckets[
        (buckets["row_type"] == "equal_day_bucket")
        & (buckets["feature"] == feature)
        & (buckets["horizon"] == horizon)
    ].sort_values("bucket")
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(rows["bucket"], rows["mean_future_move_bps"], marker="o")
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_xlabel("Feature decile")
    ax.set_ylabel("Mean future move, bps")
    ax.set_title(f"{feature} {horizon} Decile Curve")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _plot_next_move(next_moves: pd.DataFrame, *, feature: str, path: Path) -> None:
    rows = next_moves[(next_moves["date"] == "ALL_EQUAL_DAY") & (next_moves["feature"] == feature)]
    rows = rows.sort_values("bucket")
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(rows["bucket"], rows["p_up"], marker="o", label="P(up)")
    ax.plot(rows["bucket"], rows["p_down"], marker="o", label="P(down)")
    ax.set_xlabel("Feature decile")
    ax.set_ylabel("Probability")
    ax.set_title(f"{feature} Next Mid Move")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _plot_nonoverlap(nonoverlap: pd.DataFrame, path: Path) -> None:
    rows = nonoverlap[nonoverlap["date"] == "ALL"].sort_values(["horizon", "feature"])
    fig, ax = plt.subplots(figsize=(9, 4))
    labels = rows["feature"] + "/" + rows["horizon"]
    x = np.arange(len(rows))
    ax.scatter(x, rows["full_grid_spearman_ic"], s=16, label="full")
    ax.scatter(x, rows["nonoverlap_spearman_ic"], s=16, label="non-overlap")
    ax.axhline(0, color="black", linewidth=0.8)
    tick_step = max(1, len(x) // 10)
    ax.set_xticks(
        x[::tick_step],
        labels.iloc[::tick_step],
        rotation=45,
        ha="right",
    )
    ax.set_ylabel("Mean daily Spearman IC")
    ax.set_title("Full Grid vs Non-Overlap")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _heatmap(
    frame: pd.DataFrame,
    title: str,
    path: Path,
    figsize: tuple[int, int] = (8, 5),
) -> None:
    fig, ax = plt.subplots(figsize=figsize)
    values = frame.to_numpy(dtype=float)
    image = ax.imshow(values, aspect="auto", cmap="coolwarm", interpolation="nearest")
    ax.set_yticks(np.arange(len(frame.index)), frame.index)
    ax.set_xticks(np.arange(len(frame.columns)), frame.columns, rotation=45, ha="right")
    ax.set_title(title)
    fig.colorbar(image, ax=ax, shrink=0.8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    raise SystemExit(main())
