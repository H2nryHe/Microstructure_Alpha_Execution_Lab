"""Run the Phase 7 suspicious-result robustness audit.

This script intentionally writes only under reports/phase7/audit and does not
modify the frozen Phase 7 baseline outputs.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
from bisect import bisect_left
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from microalpha.config import load_yaml_config
from microalpha.pipeline.registry import DEVELOPMENT_DATES
from microalpha.research.phase7 import (
    EXPECTED_SNAPSHOT_HASH,
    FEATURE_VERSION,
    HORIZONS,
    LABEL_VERSION,
    MOVE_BPS_COLUMNS,
    NONOVERLAP_STRIDES,
    RETURN_COLUMNS,
    STATE_FEATURES,
    load_snapshot_manifest,
    nonoverlap_mask,
    pearson_ic,
    spearman_ic,
    summarize_daily_values,
    verify_pre_phase7_snapshot,
)
from microalpha.utils.hashing import hash_config

STATE_COLUMNS = ["best_bid", "bid_sz_1", "best_ask", "ask_sz_1"]
AUDIT_DATES = ["2024-01-01", "2024-11-01", "2025-02-01", "2025-11-01"]
BUCKET_CHECK_DATES = ["2024-01-01", "2024-06-01", "2024-12-01", "2025-06-01", "2025-12-01"]
MANUAL_QI_QUANTILES = [0.01, 0.25, 0.50, 0.75, 0.99]
LABEL_CHECK_HORIZONS = ["100ms", "1s", "5s", "30s"]
TEMPORAL_CONTROL_LAG_ROWS = 3000
FLOAT_TOLERANCE = 1e-12


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--derived-root", default="/tmp/microalpha-multiday/derived")
    parser.add_argument("--snapshot", default="data/manifests/pre_phase7_research_snapshot.json")
    parser.add_argument("--plan", default="data/manifests/phase7_research_plan.yaml")
    parser.add_argument("--phase7-dir", default="reports/phase7")
    parser.add_argument("--output-dir", default="reports/phase7/audit")
    parser.add_argument("--clean", action="store_true")
    return parser.parse_args()


def feature_path(root: Path, date: str) -> Path:
    return root / f"date={date}" / f"features_{FEATURE_VERSION}.parquet"


def label_path(root: Path, date: str) -> Path:
    return root / f"date={date}" / f"labels_{LABEL_VERSION}.parquet"


def research_path(root: Path, date: str) -> Path:
    return root / f"date={date}" / "research_100ms.parquet"


def read_day(root: Path, date: str) -> pd.DataFrame:
    feature_cols = [
        "observation_time",
        "feature_cutoff_time",
        "book_observation_time",
        "book_event_time",
        "book_source_row_number",
        "mid",
        "spread_bps",
        "qi_1",
        "di_5",
        "di_10",
        "microprice_deviation_bps",
        "ofi_1s",
    ]
    label_cols = [
        "observation_time",
        "feature_cutoff_time",
        "mid",
        "book_observation_time",
        "book_event_time",
        "book_source_row_number",
    ]
    for horizon in HORIZONS:
        label_cols.extend(
            [
                f"target_time_{horizon}",
                f"actual_label_time_{horizon}",
                f"label_delay_ms_{horizon}",
                RETURN_COLUMNS[horizon],
                MOVE_BPS_COLUMNS[horizon],
            ]
        )
    research_cols = [
        "observation_time",
        "feature_cutoff_time",
        "book_observation_time",
        "book_event_time",
        "book_source_row_number",
        "best_bid",
        "bid_sz_1",
        "best_ask",
        "ask_sz_1",
        "mid",
        "spread",
    ]
    features = pd.read_parquet(feature_path(root, date), columns=feature_cols)
    labels = pd.read_parquet(label_path(root, date), columns=label_cols)
    research = pd.read_parquet(research_path(root, date), columns=research_cols)
    if (
        not features["observation_time"]
        .reset_index(drop=True)
        .equals(labels["observation_time"].reset_index(drop=True))
    ):
        raise ValueError(f"Feature/label timestamp mismatch for {date}")
    if (
        not features["observation_time"]
        .reset_index(drop=True)
        .equals(research["observation_time"].reset_index(drop=True))
    ):
        raise ValueError(f"Feature/research timestamp mismatch for {date}")
    labels = labels.drop(
        columns=[
            "observation_time",
            "feature_cutoff_time",
            "mid",
            "book_observation_time",
            "book_event_time",
            "book_source_row_number",
        ]
    )
    research = research.drop(
        columns=[
            "observation_time",
            "feature_cutoff_time",
            "book_observation_time",
            "book_event_time",
            "book_source_row_number",
            "mid",
        ]
    )
    frame = pd.concat(
        [
            features.reset_index(drop=True),
            labels.reset_index(drop=True),
            research.reset_index(drop=True),
        ],
        axis=1,
    )
    numeric = set(STATE_FEATURES) | {"ofi_1s", "mid", "spread_bps", "spread"} | set(STATE_COLUMNS)
    numeric |= set(RETURN_COLUMNS.values()) | set(MOVE_BPS_COLUMNS.values())
    for column in sorted(numeric & set(frame.columns)):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("float64")
    return frame


def bbo_changed(frame: pd.DataFrame, *, include_first: bool) -> pd.Series:
    states = frame[STATE_COLUMNS]
    changed = states.ne(states.shift(1)).any(axis=1)
    changed &= states.notna().all(axis=1)
    if include_first and len(changed):
        changed.iloc[0] = bool(states.iloc[0].notna().all())
    elif len(changed):
        changed.iloc[0] = False
    return changed


def summarize_state_ic(
    *,
    label: str,
    feature: str,
    date: str,
    frame: pd.DataFrame,
    mask: pd.Series,
    nonoverlap: pd.DataFrame,
) -> dict[str, Any]:
    full = spearman_ic(frame[feature], frame[label])
    stride = NONOVERLAP_STRIDES[label.replace("ret_fwd_", "")]
    non_mask = nonoverlap_mask(len(frame), stride)
    non = spearman_ic(frame.loc[non_mask, feature], frame.loc[non_mask, label])
    robust = spearman_ic(frame.loc[mask, feature], frame.loc[mask, label])
    phase7_non = nonoverlap[
        (nonoverlap["date"] == date)
        & (nonoverlap["feature"] == feature)
        & (nonoverlap["horizon"] == label.replace("ret_fwd_", ""))
    ]
    if not phase7_non.empty:
        full = float(phase7_non.iloc[0]["full_grid_spearman_ic"])
        non = float(phase7_non.iloc[0]["nonoverlap_spearman_ic"])
    return {
        "date": date,
        "feature": feature,
        "horizon": label.replace("ret_fwd_", ""),
        "full_grid_daily_ic": full,
        "nonoverlap_daily_ic": non,
        "robust_daily_ic": robust,
        "robust_row_count": int(mask.sum()),
        "fixed_clock_row_count": int(len(frame)),
        "retained_fraction": float(mask.mean()),
    }


def aggregate_robust(rows: pd.DataFrame, robust_name: str) -> pd.DataFrame:
    summaries = []
    for keys, group in rows.groupby(["feature", "horizon"], sort=True):
        feature, horizon = keys
        robust = summarize_daily_values(group["robust_daily_ic"])
        full = summarize_daily_values(group["full_grid_daily_ic"])
        non = summarize_daily_values(group["nonoverlap_daily_ic"])
        summaries.append(
            {
                "date": "ALL",
                "feature": feature,
                "horizon": horizon,
                "full_grid_daily_ic": full["mean"],
                "nonoverlap_daily_ic": non["mean"],
                "robust_daily_ic": robust["mean"],
                "robust_row_count": int(group["robust_row_count"].sum()),
                "fixed_clock_row_count": int(group["fixed_clock_row_count"].sum()),
                "retained_fraction": float(
                    group["robust_row_count"].sum() / group["fixed_clock_row_count"].sum()
                ),
                "positive_day_count": robust["positive_days"],
                "median": robust["median"],
                "min": robust["min"],
                "max": robust["max"],
                "statistic": robust_name,
            }
        )
    output = rows.copy()
    output["positive_day_count"] = ""
    output["median"] = ""
    output["min"] = ""
    output["max"] = ""
    output["statistic"] = robust_name
    return pd.concat([output, pd.DataFrame(summaries)], ignore_index=True)


def manual_selection(date: str, frame: pd.DataFrame) -> pd.DataFrame:
    valid = frame.dropna(subset=["qi_1", "ret_fwd_30s"]).copy()
    selected_indices = []
    for quantile in MANUAL_QI_QUANTILES:
        target = float(valid["qi_1"].quantile(quantile))
        distances = (valid["qi_1"] - target).abs()
        selected_indices.append(int(distances.sort_values(kind="mergesort").index[0]))
    selected = frame.loc[selected_indices].copy()
    selected.insert(0, "date", date)
    selected.insert(1, "selection_quantile", MANUAL_QI_QUANTILES)
    return selected


def prepare_time_index(frame: pd.DataFrame) -> tuple[list[pd.Timestamp], list[float]]:
    times = pd.to_datetime(frame["feature_cutoff_time"], utc=True).tolist()
    mids = frame["mid"].astype(float).tolist()
    return times, mids


def independent_future(
    *,
    times: list[pd.Timestamp],
    mids: list[float],
    current_time: pd.Timestamp,
    horizon: str,
    max_delay_ms: int,
) -> tuple[pd.Timestamp, pd.Timestamp | None, float | None, float | None, int | None]:
    target = current_time + pd.Timedelta(milliseconds=horizon_to_ms(horizon))
    index = bisect_left(times, target)
    if index >= len(times):
        return target, None, None, None, None
    actual = times[index]
    delay = int(round((actual - target).total_seconds() * 1000))
    if actual.date() != current_time.date() or delay > max_delay_ms:
        return target, None, None, None, None
    future_mid = float(mids[index])
    return (
        target,
        actual,
        future_mid,
        math.log(future_mid / float(mids[times.index(current_time)])),
        delay,
    )


def horizon_to_ms(horizon: str) -> int:
    return {"100ms": 100, "500ms": 500, "1s": 1000, "5s": 5000, "30s": 30000}[horizon]


def independent_label_checks(
    selected: pd.DataFrame, day_frame: pd.DataFrame
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    times, mids = prepare_time_index(day_frame)
    index_by_time = {time: index for index, time in enumerate(times)}
    lineage_rows = []
    label_rows = []
    for _, row in selected.iterrows():
        t = pd.to_datetime(row["feature_cutoff_time"], utc=True)
        current_index = index_by_time[t]
        mid_t = float(row["mid"])
        lineage: dict[str, Any] = {
            "date": row["date"],
            "selection_quantile": row["selection_quantile"],
            "feature_cutoff_time": row["feature_cutoff_time"],
            "observation_time": row["observation_time"],
            "source_row_number": row["book_source_row_number"],
            "best_bid": row["best_bid"],
            "best_bid_quantity": row["bid_sz_1"],
            "best_ask": row["best_ask"],
            "best_ask_quantity": row["ask_sz_1"],
            "mid_T": mid_t,
            "qi_1": row["qi_1"],
            "microprice_deviation_bps": row["microprice_deviation_bps"],
            "feature_book_observation_time": row["book_observation_time"],
            "feature_book_event_time": row["book_event_time"],
            "feature_source_leq_T": str(row["book_observation_time"])
            <= str(row["feature_cutoff_time"]),
        }
        for horizon in LABEL_CHECK_HORIZONS:
            target = t + pd.Timedelta(milliseconds=horizon_to_ms(horizon))
            future_index = bisect_left(times, target)
            actual = times[future_index] if future_index < len(times) else None
            delay = (
                int(round((actual - target).total_seconds() * 1000)) if actual is not None else None
            )
            future_mid = (
                float(mids[future_index]) if actual is not None and delay <= 100 else math.nan
            )
            independent_ret = (
                math.log(future_mid / mid_t)
                if actual is not None and delay is not None and delay <= 100
                else math.nan
            )
            stored_ret = (
                float(row[RETURN_COLUMNS[horizon]])
                if pd.notna(row[RETURN_COLUMNS[horizon]])
                else math.nan
            )
            diff = (
                abs(independent_ret - stored_ret)
                if math.isfinite(independent_ret) and math.isfinite(stored_ret)
                else math.nan
            )
            label_rows.append(
                {
                    "date": row["date"],
                    "feature_cutoff_time": row["feature_cutoff_time"],
                    "horizon": horizon,
                    "target_time": target.isoformat(),
                    "independent_actual_label_time": actual.isoformat()
                    if actual is not None
                    else "",
                    "stored_actual_label_time": row[f"actual_label_time_{horizon}"],
                    "independent_label_delay_ms": delay,
                    "stored_label_delay_ms": row[f"label_delay_ms_{horizon}"],
                    "mid_T": mid_t,
                    "independent_future_mid": future_mid,
                    "stored_future_mid_reconstructed": mid_t * math.exp(stored_ret)
                    if math.isfinite(stored_ret)
                    else math.nan,
                    "independent_ret": independent_ret,
                    "stored_ret": stored_ret,
                    "absolute_difference": diff,
                    "within_tolerance": bool(math.isfinite(diff) and diff <= FLOAT_TOLERANCE),
                    "target_time_gt_T": target > t,
                    "actual_time_gte_target": bool(actual is not None and actual >= target),
                    "delay_within_tolerance": bool(delay is not None and delay <= 100),
                    "future_index_gt_current_index": bool(
                        actual is not None and future_index > current_index
                    ),
                }
            )
            if horizon == "1s":
                lineage.update(
                    {
                        "target_time_1s": target.isoformat(),
                        "actual_label_time_1s": actual.isoformat() if actual is not None else "",
                        "label_delay_ms_1s": delay,
                        "mid_future_1s": future_mid,
                        "ret_fwd_1s": row["ret_fwd_1s"],
                        "target_time_gt_T": target > t,
                        "actual_label_time_gte_target": bool(
                            actual is not None and actual >= target
                        ),
                        "label_delay_within_tolerance": bool(delay is not None and delay <= 100),
                        "mid_future_from_future_state": bool(
                            actual is not None and future_index > current_index
                        ),
                    }
                )
        lineage_rows.append(lineage)
    return lineage_rows, label_rows


def independent_buckets(
    date: str, frame: pd.DataFrame, production: pd.DataFrame
) -> list[dict[str, Any]]:
    valid_feature = frame["qi_1"].notna()
    ordered = frame.loc[valid_feature, ["qi_1", "ret_fwd_1s", "future_mid_move_bps_1s"]].copy()
    ordered["_original_index"] = ordered.index
    ordered = ordered.sort_values(["qi_1", "_original_index"], kind="mergesort")
    n = len(ordered)
    ordered["bucket"] = (np.floor(np.arange(n) * 10 / n).astype(int) + 1).clip(1, 10)
    rows = []
    prod = production[
        (production["date"] == date)
        & (production["feature"] == "qi_1")
        & (production["horizon"] == "1s")
        & (production["row_type"] == "daily_bucket")
    ]
    for bucket in range(1, 11):
        subset = ordered[ordered["bucket"] == bucket].dropna(
            subset=["ret_fwd_1s", "future_mid_move_bps_1s"]
        )
        prod_row = prod[prod["bucket"] == bucket].iloc[0]
        mean_ret = float(subset["ret_fwd_1s"].mean())
        mean_move = float(subset["future_mid_move_bps_1s"].mean())
        rows.append(
            {
                "date": date,
                "feature": "qi_1",
                "horizon": "1s",
                "bucket": bucket,
                "independent_mean_ret_fwd_1s": mean_ret,
                "production_mean_ret_fwd_1s": float(prod_row["mean_future_return"]),
                "mean_return_abs_diff": abs(mean_ret - float(prod_row["mean_future_return"])),
                "independent_mean_future_move_bps": mean_move,
                "production_mean_future_move_bps": float(prod_row["mean_future_move_bps"]),
                "mean_move_abs_diff": abs(mean_move - float(prod_row["mean_future_move_bps"])),
                "independent_count": int(len(subset)),
                "production_count": int(prod_row["observation_count"]),
                "count_matches": int(len(subset)) == int(prod_row["observation_count"]),
                "bucket_low_feature_to_high_feature": True,
                "labels_used_for_bucket_assignment": False,
                "sorted_by_future_return": False,
            }
        )
    return rows


def feature_redundancy(date: str, frame: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for left_index, left in enumerate(STATE_FEATURES):
        for right in STATE_FEATURES[left_index + 1 :]:
            rows.append(
                {
                    "date": date,
                    "row_type": "daily_feature_correlation",
                    "left": left,
                    "right": right,
                    "spearman": spearman_ic(frame[left], frame[right]),
                }
            )
    for feature in ["di_5", "di_10", "ofi_1s"]:
        residual = rank_residual(frame[feature], frame["qi_1"])
        rows.append(
            {
                "date": date,
                "row_type": "daily_rank_residual_ic",
                "left": f"{feature}_residualized_against_qi_1",
                "right": "ret_fwd_1s",
                "spearman": spearman_ic(residual, frame["ret_fwd_1s"]),
            }
        )
    return rows


def rank_residual(x: pd.Series, base: pd.Series) -> pd.Series:
    valid = x.notna() & base.notna()
    residual = pd.Series(np.nan, index=x.index, dtype="float64")
    if int(valid.sum()) < 3:
        return residual
    xr = x[valid].rank(method="average").to_numpy(dtype=float)
    br = base[valid].rank(method="average").to_numpy(dtype=float)
    slope = float(np.cov(br, xr, ddof=0)[0, 1] / np.var(br))
    intercept = float(xr.mean() - slope * br.mean())
    residual.loc[valid] = xr - (intercept + slope * br)
    return residual


def summarize_feature_redundancy(rows: pd.DataFrame) -> pd.DataFrame:
    summaries = []
    for keys, group in rows.groupby(["row_type", "left", "right"], sort=True):
        row_type, left, right = keys
        values = summarize_daily_values(group["spearman"])
        summaries.append(
            {
                "date": "ALL",
                "row_type": row_type.replace("daily_", "summary_"),
                "left": left,
                "right": right,
                "spearman": values["mean"],
                "mean": values["mean"],
                "median": values["median"],
                "min": values["min"],
                "max": values["max"],
                "positive_days": values["positive_days"],
                "negative_days": values["negative_days"],
            }
        )
    return pd.concat([rows, pd.DataFrame(summaries)], ignore_index=True)


def return_discreteness(date: str, frame: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    qi_buckets = independent_bucket_assignments(frame["qi_1"])
    for horizon in HORIZONS:
        ret = frame[RETURN_COLUMNS[horizon]]
        valid = ret.dropna()
        rows.append(
            {
                "date": date,
                "horizon": horizon,
                "bucket": "",
                "row_type": "daily",
                "fraction_ret_zero": float((valid == 0).mean()) if len(valid) else math.nan,
                "unique_forward_return_values": int(valid.nunique()) if len(valid) else 0,
                "fraction_unchanged_mid": float((valid == 0).mean()) if len(valid) else math.nan,
                "p_return_gt_0": float((valid > 0).mean()) if len(valid) else math.nan,
                "p_return_lt_0": float((valid < 0).mean()) if len(valid) else math.nan,
                "count": int(len(valid)),
            }
        )
        for bucket in range(1, 11):
            subset = ret[qi_buckets == bucket].dropna()
            rows.append(
                {
                    "date": date,
                    "horizon": horizon,
                    "bucket": bucket,
                    "row_type": "qi_1_decile",
                    "fraction_ret_zero": float((subset == 0).mean()) if len(subset) else math.nan,
                    "unique_forward_return_values": int(subset.nunique()) if len(subset) else 0,
                    "fraction_unchanged_mid": float((subset == 0).mean())
                    if len(subset)
                    else math.nan,
                    "p_return_gt_0": float((subset > 0).mean()) if len(subset) else math.nan,
                    "p_return_lt_0": float((subset < 0).mean()) if len(subset) else math.nan,
                    "count": int(len(subset)),
                }
            )
    return rows


def independent_bucket_assignments(feature: pd.Series) -> pd.Series:
    valid = feature.notna()
    ordered = pd.DataFrame({"feature": feature[valid]})
    ordered["_original_index"] = ordered.index
    ordered = ordered.sort_values(["feature", "_original_index"], kind="mergesort")
    buckets = pd.Series(pd.NA, index=feature.index, dtype="Int64")
    n = len(ordered)
    if n:
        buckets.loc[ordered.index] = (np.floor(np.arange(n) * 10 / n).astype(int) + 1).clip(1, 10)
    return buckets


def spread_diagnostics(date: str, frame: pd.DataFrame) -> list[dict[str, Any]]:
    spread = frame["spread_bps"].dropna()
    min_spread = float(spread.min())
    rows = []
    base = {
        "date": date,
        "median_spread_bps": float(spread.median()),
        "p95_spread_bps": float(spread.quantile(0.95)),
        "minimum_spread_bps": min_spread,
        "fraction_at_minimum_spread": float((spread == min_spread).mean()),
        "distinct_spread_values": int(spread.nunique()),
    }
    for horizon in HORIZONS:
        for group_name, mask in [
            ("minimum_spread", frame["spread_bps"] == min_spread),
            ("wider_spread", frame["spread_bps"] > min_spread),
        ]:
            rows.append(
                {
                    **base,
                    "horizon": horizon,
                    "group": group_name,
                    "qi_1_spearman_ic": spearman_ic(
                        frame.loc[mask, "qi_1"],
                        frame.loc[mask, RETURN_COLUMNS[horizon]],
                    ),
                    "count": int(mask.sum()),
                }
            )
    return rows


def temporal_control(date: str, frame: pd.DataFrame) -> dict[str, Any]:
    lagged = frame["qi_1"].shift(TEMPORAL_CONTROL_LAG_ROWS)
    return {
        "date": date,
        "control": "qi_1_lagged_5_minutes",
        "horizon": "1s",
        "lag_rows": TEMPORAL_CONTROL_LAG_ROWS,
        "lag_duration": "5 minutes on 100ms fixed grid",
        "spearman_ic": spearman_ic(lagged, frame["ret_fwd_1s"]),
        "pearson_ic": pearson_ic(lagged, frame["ret_fwd_1s"]),
    }


def nonoverlap_diagnostics(nonoverlap: pd.DataFrame) -> dict[str, Any]:
    aggregate = nonoverlap[nonoverlap["date"] == "ALL"].copy()
    daily = nonoverlap[nonoverlap["date"] != "ALL"].copy()
    aggregate_abs = (aggregate["nonoverlap_spearman_ic"] - aggregate["full_grid_spearman_ic"]).abs()
    daily_abs = (daily["nonoverlap_spearman_ic"] - daily["full_grid_spearman_ic"]).abs()
    return {
        "previous_phase7_summary_value_interpretation": (
            "The Phase 7 summary value equals aggregate_pair_mean_abs_difference "
            "computed from the 30 date == ALL rows."
        ),
        "aggregate_pair_mean_abs_difference": float(aggregate_abs.mean()),
        "daily_pair_mean_abs_difference": float(daily_abs.mean()),
        "median_daily_absolute_difference": float(daily_abs.median()),
        "p95_daily_absolute_difference": float(daily_abs.quantile(0.95)),
        "max_daily_absolute_difference": float(daily_abs.max()),
        "aggregate_pair_count": int(len(aggregate_abs)),
        "daily_pair_count": int(len(daily_abs)),
    }


def equal_day_aggregation_diagnostics(bucket_results: pd.DataFrame) -> dict[str, Any]:
    daily = bucket_results[bucket_results["row_type"] == "daily_bucket"]
    aggregate = bucket_results[
        (bucket_results["date"] == "ALL_EQUAL_DAY")
        & (bucket_results["row_type"] == "equal_day_bucket")
    ]
    checks = []
    for keys, group in daily.groupby(["feature", "horizon", "bucket"], sort=True):
        feature, horizon, bucket = keys
        aggregate_row = aggregate[
            (aggregate["feature"] == feature)
            & (aggregate["horizon"] == horizon)
            & (aggregate["bucket"] == bucket)
        ].iloc[0]
        checks.append(
            {
                "feature": feature,
                "horizon": horizon,
                "bucket": int(bucket),
                "return_abs_diff": abs(
                    float(group["mean_future_return"].mean())
                    - float(aggregate_row["mean_future_return"])
                ),
                "move_abs_diff": abs(
                    float(group["mean_future_move_bps"].mean())
                    - float(aggregate_row["mean_future_move_bps"])
                ),
            }
        )
    check_frame = pd.DataFrame(checks)
    return {
        "checked_rows": int(len(check_frame)),
        "max_mean_return_abs_diff": float(check_frame["return_abs_diff"].max()),
        "max_mean_move_abs_diff": float(check_frame["move_abs_diff"].max()),
        "method": "ALL_EQUAL_DAY rows equal the arithmetic mean of daily bucket means.",
    }


def audit_hash(output_dir: Path) -> str:
    files = [
        "changed_state_ic.csv",
        "unique_state_ic.csv",
        "manual_lineage_audit.csv",
        "independent_label_check.csv",
        "independent_bucket_check.csv",
        "feature_redundancy.csv",
        "return_discreteness.csv",
        "spread_diagnostics.csv",
        "README.md",
        "audit_summary.json",
    ]
    payload: dict[str, Any] = {}
    for file in files:
        content = (output_dir / file).read_text(encoding="utf-8")
        if file == "audit_summary.json":
            summary = json.loads(content)
            summary.pop("audit_result_hash", None)
            payload[file] = summary
        else:
            payload[file] = content
    return hash_config(payload)


def write_csv(path: Path, rows: list[dict[str, Any]] | pd.DataFrame) -> pd.DataFrame:
    frame = rows if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    frame.to_csv(path, index=False, float_format="%.12g", na_rep="", lineterminator="\n")
    return frame


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    if args.clean and output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    plan = load_yaml_config(args.plan)
    snapshot = load_snapshot_manifest(args.snapshot)
    snapshot_check = verify_pre_phase7_snapshot(snapshot)
    if snapshot_check["snapshot_hash"] != EXPECTED_SNAPSHOT_HASH:
        raise ValueError("Unexpected frozen snapshot hash")
    dates = list(DEVELOPMENT_DATES)
    root = Path(args.derived_root)
    phase7_dir = Path(args.phase7_dir)
    nonoverlap = pd.read_csv(phase7_dir / "nonoverlap_ic.csv")
    production_buckets = pd.read_csv(phase7_dir / "bucket_results.csv")

    changed_rows: list[dict[str, Any]] = []
    unique_rows: list[dict[str, Any]] = []
    lineage_rows: list[dict[str, Any]] = []
    label_rows: list[dict[str, Any]] = []
    bucket_rows: list[dict[str, Any]] = []
    redundancy_rows: list[dict[str, Any]] = []
    discreteness_rows: list[dict[str, Any]] = []
    spread_rows: list[dict[str, Any]] = []
    temporal_rows: list[dict[str, Any]] = []

    for index, date in enumerate(dates, start=1):
        print(f"phase7 audit processing {date} ({index}/{len(dates)})", flush=True)
        frame = read_day(root, date)
        changed_mask = bbo_changed(frame, include_first=False)
        unique_mask = bbo_changed(frame, include_first=True)
        for feature in STATE_FEATURES:
            for horizon in HORIZONS:
                label = RETURN_COLUMNS[horizon]
                changed_rows.append(
                    summarize_state_ic(
                        label=label,
                        feature=feature,
                        date=date,
                        frame=frame,
                        mask=changed_mask,
                        nonoverlap=nonoverlap,
                    )
                )
                unique_rows.append(
                    summarize_state_ic(
                        label=label,
                        feature=feature,
                        date=date,
                        frame=frame,
                        mask=unique_mask,
                        nonoverlap=nonoverlap,
                    )
                )
        if date in AUDIT_DATES:
            selected = manual_selection(date, frame)
            day_lineage, day_labels = independent_label_checks(selected, frame)
            lineage_rows.extend(day_lineage)
            label_rows.extend(day_labels)
        if date in BUCKET_CHECK_DATES:
            bucket_rows.extend(independent_buckets(date, frame, production_buckets))
        redundancy_rows.extend(feature_redundancy(date, frame))
        discreteness_rows.extend(return_discreteness(date, frame))
        spread_rows.extend(spread_diagnostics(date, frame))
        temporal_rows.append(temporal_control(date, frame))

    changed = write_csv(
        output_dir / "changed_state_ic.csv",
        aggregate_robust(pd.DataFrame(changed_rows), "changed_state"),
    )
    unique = write_csv(
        output_dir / "unique_state_ic.csv",
        aggregate_robust(pd.DataFrame(unique_rows), "unique_state"),
    )
    lineage = write_csv(output_dir / "manual_lineage_audit.csv", lineage_rows)
    labels = write_csv(output_dir / "independent_label_check.csv", label_rows)
    buckets = write_csv(output_dir / "independent_bucket_check.csv", bucket_rows)
    redundancy = write_csv(
        output_dir / "feature_redundancy.csv",
        summarize_feature_redundancy(pd.DataFrame(redundancy_rows)),
    )
    discreteness = write_csv(output_dir / "return_discreteness.csv", discreteness_rows)
    spreads = write_csv(output_dir / "spread_diagnostics.csv", spread_rows)

    temporal = pd.DataFrame(temporal_rows)
    temporal_summary = summarize_daily_values(temporal["spearman_ic"])
    label_failures = int((~labels["within_tolerance"]).sum())
    bucket_failures = int(
        (
            (buckets["mean_return_abs_diff"] > FLOAT_TOLERANCE)
            | (buckets["mean_move_abs_diff"] > 1e-9)
            | (~buckets["count_matches"])
        ).sum()
    )
    changed_summary = changed[changed["date"] == "ALL"]
    unique_summary = unique[unique["date"] == "ALL"]
    nonoverlap_summary = nonoverlap_diagnostics(nonoverlap)
    equal_day_summary = equal_day_aggregation_diagnostics(production_buckets)

    summary = {
        "phase": "Phase 7 Suspicious-Result / Robustness Audit",
        "audit_status": "PASS"
        if label_failures == 0
        and bucket_failures == 0
        and bool(lineage["feature_source_leq_T"].all())
        else "FAIL",
        "snapshot_hash": EXPECTED_SNAPSHOT_HASH,
        "snapshot_verification": snapshot_check,
        "phase7_research_plan_hash": hash_config(plan),
        "phase7_results_preserved": True,
        "holdout_accessed": False,
        "nonoverlap_diagnostics": nonoverlap_summary,
        "changed_state": {
            "summary_rows": int(len(changed_summary)),
            "directionally_consistent_tests": int((changed_summary["robust_daily_ic"] > 0).sum()),
            "minimum_changed_state_mean_ic": float(changed_summary["robust_daily_ic"].min()),
            "median_retained_fraction": float(changed_summary["retained_fraction"].median()),
        },
        "unique_state": {
            "summary_rows": int(len(unique_summary)),
            "directionally_consistent_tests": int((unique_summary["robust_daily_ic"] > 0).sum()),
            "minimum_unique_state_mean_ic": float(unique_summary["robust_daily_ic"].min()),
            "median_retained_fraction": float(unique_summary["retained_fraction"].median()),
        },
        "manual_lineage": {
            "rows": int(len(lineage)),
            "feature_source_leq_T_all": bool(lineage["feature_source_leq_T"].all()),
            "target_time_gt_T_all": bool(lineage["target_time_gt_T"].all()),
            "actual_label_time_gte_target_all": bool(lineage["actual_label_time_gte_target"].all()),
            "label_delay_within_tolerance_all": bool(lineage["label_delay_within_tolerance"].all()),
            "mid_future_from_future_state_all": bool(lineage["mid_future_from_future_state"].all()),
        },
        "independent_label_check": {
            "rows": int(len(labels)),
            "failures": label_failures,
            "max_absolute_difference": float(labels["absolute_difference"].max()),
            "tolerance": FLOAT_TOLERANCE,
        },
        "independent_bucket_check": {
            "dates": BUCKET_CHECK_DATES,
            "rows": int(len(buckets)),
            "failures": bucket_failures,
            "max_mean_return_abs_diff": float(buckets["mean_return_abs_diff"].max()),
            "max_mean_move_abs_diff": float(buckets["mean_move_abs_diff"].max()),
            "labels_used_for_bucket_assignment": False,
            "equal_day_aggregation_checked_against_daily_bucket_rows": True,
            "equal_day_aggregation": equal_day_summary,
        },
        "feature_redundancy": {
            "relationship": (
                "microprice - mid = spread * qi_1 / 2, so "
                "microprice_deviation_bps = spread_bps * qi_1 / 2."
            ),
            "qi_1_microprice_rank_spearman_mean": float(
                redundancy[
                    (redundancy["date"] == "ALL")
                    & (redundancy["left"] == "qi_1")
                    & (redundancy["right"] == "microprice_deviation_bps")
                ]["mean"].iloc[0]
            ),
        },
        "temporal_negative_control": {
            "control": "qi_1_lagged_5_minutes",
            "mean_spearman_ic": temporal_summary["mean"],
            "median_spearman_ic": temporal_summary["median"],
            "positive_days": temporal_summary["positive_days"],
            "negative_days": temporal_summary["negative_days"],
            "t_stat": temporal_summary["t_stat"],
            "raw_p_value": temporal_summary["raw_p_value"],
        },
        "return_discreteness_rows": int(len(discreteness)),
        "spread_diagnostic_rows": int(len(spreads)),
        "acceptance_notes": [
            "Original Phase 7 primary results were not overwritten.",
            (
                "Changed-state and unique-state diagnostics are robustness checks, "
                "not replacement primary results."
            ),
            "Independent label recomputation does not call the production label helper.",
            "Independent bucket reconstruction does not use the production bucket helper.",
            "No Phase 8, model training, or backtest was performed.",
        ],
    }
    (output_dir / "README.md").write_text(readme_text(summary), encoding="utf-8")
    (output_dir / "audit_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    result_hash = audit_hash(output_dir)
    summary["audit_result_hash"] = result_hash
    (output_dir / "audit_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {"audit_status": summary["audit_status"], "audit_result_hash": result_hash},
            sort_keys=True,
        )
    )
    return 0 if summary["audit_status"] == "PASS" else 1


def readme_text(summary: dict[str, Any]) -> str:
    changed = summary["changed_state"]["directionally_consistent_tests"]
    unique = summary["unique_state"]["directionally_consistent_tests"]
    return f"""# Phase 7 Suspicious-Result / Robustness Audit

Audit status: {summary["audit_status"]}

Frozen Phase 7 baseline outputs were preserved. New audit outputs live only in this directory.

Snapshot hash: `{summary["snapshot_hash"]}`

Key checks:

- Independent label recomputation failures: {summary["independent_label_check"]["failures"]}
- Independent bucket reconstruction failures: {summary["independent_bucket_check"]["failures"]}
- Changed-state positive mean IC tests: {changed}/20
- Unique-state positive mean IC tests: {unique}/20
- Temporal control: qi_1 lagged by 5 minutes.

This audit is diagnostic only. It does not modify the Phase 7 primary family and does
not make trading or profitability claims.
"""


if __name__ == "__main__":
    raise SystemExit(main())
