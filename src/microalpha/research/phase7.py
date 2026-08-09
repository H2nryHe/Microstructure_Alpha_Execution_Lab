"""Phase 7 baseline statistical signal research helpers."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from scipy import stats

from microalpha.pipeline.multiday import aggregate_snapshot_hash
from microalpha.pipeline.registry import DEVELOPMENT_DATES
from microalpha.utils.hashing import hash_config

EXPECTED_SNAPSHOT_HASH = "0bcdb7eddebbe83458998eff78844471afb78fc66d249a53aeb25667bebd803a"
FEATURE_VERSION = "microstructure_v1"
LABEL_VERSION = "microstructure_labels_v1"
PLAN_PATH = Path("data/manifests/phase7_research_plan.yaml")
SNAPSHOT_PATH = Path("data/manifests/pre_phase7_research_snapshot.json")

HORIZONS = ("100ms", "500ms", "1s", "5s", "30s")
STATE_FEATURES = ("qi_1", "di_5", "di_10", "microprice_deviation_bps")
FLOW_FEATURES_BY_HORIZON = {
    "100ms": ("ofi_100ms", "trade_imbalance_100ms"),
    "500ms": ("ofi_500ms", "trade_imbalance_500ms"),
    "1s": ("ofi_1s", "trade_imbalance_1s"),
    "5s": ("ofi_5s", "trade_imbalance_5s"),
    "30s": ("ofi_30s", "trade_imbalance_30s"),
}
RETURN_COLUMNS = {
    horizon: f"ret_fwd_{horizon}" for horizon in HORIZONS
}
MOVE_BPS_COLUMNS = {
    horizon: f"future_mid_move_bps_{horizon}" for horizon in HORIZONS
}
DIRECTION_COLUMNS = {
    horizon: f"direction_{horizon}" for horizon in HORIZONS
}
NONOVERLAP_STRIDES = {"100ms": 1, "500ms": 5, "1s": 10, "5s": 50, "30s": 300}
NEXT_MOVE_FEATURES = ("qi_1", "microprice_deviation_bps", "di_5", "di_10", "ofi_100ms")
NEXT_MOVE_PRIMARY_FEATURES = {"qi_1", "microprice_deviation_bps"}
NEGATIVE_CONTROL_FEATURE = "qi_1"
NEGATIVE_CONTROL_HORIZON = "1s"
NEGATIVE_CONTROL_SEED = 7007


@dataclass(frozen=True)
class PrimaryTest:
    feature: str
    horizon: str
    label: str
    move_bps: str
    family: str


def primary_tests() -> list[PrimaryTest]:
    tests: list[PrimaryTest] = []
    for horizon in HORIZONS:
        for feature in (*STATE_FEATURES, *FLOW_FEATURES_BY_HORIZON[horizon]):
            tests.append(
                PrimaryTest(
                    feature=feature,
                    horizon=horizon,
                    label=RETURN_COLUMNS[horizon],
                    move_bps=MOVE_BPS_COLUMNS[horizon],
                    family="state" if feature in STATE_FEATURES else "matched_flow",
                )
            )
    return tests


def load_snapshot_manifest(path: str | Path = SNAPSHOT_PATH) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as file:
        return json.load(file)


def verify_pre_phase7_snapshot(
    manifest: dict[str, Any],
    *,
    expected_hash: str = EXPECTED_SNAPSHOT_HASH,
) -> dict[str, Any]:
    """Reject snapshots that are not the frozen development-only input set."""

    recorded_hash = manifest.get("snapshot_hash")
    computed_hash = aggregate_snapshot_hash(manifest)
    if recorded_hash != expected_hash:
        raise ValueError(
            f"Snapshot hash mismatch: recorded {recorded_hash}, expected {expected_hash}"
        )
    if computed_hash != expected_hash:
        raise ValueError(
            f"Snapshot logical hash mismatch: computed {computed_hash}, expected {expected_hash}"
        )
    included_dates = manifest.get("included_dates", [])
    if any(str(date).startswith("2026-") for date in included_dates):
        raise ValueError("Snapshot includes a 2026 holdout date")
    expected_dates = list(DEVELOPMENT_DATES)
    if included_dates != expected_dates:
        raise ValueError(
            "Snapshot development dates are not the frozen 2024-2025 first-of-month set"
        )
    if manifest.get("excluded_dates"):
        raise ValueError("Snapshot contains excluded development dates")
    if manifest.get("failed_dates"):
        raise ValueError("Snapshot contains failed development dates")
    if manifest.get("dataset_role") != "development":
        raise ValueError("Snapshot dataset_role must be development")
    if manifest.get("feature_version") != FEATURE_VERSION:
        raise ValueError(f"Unexpected feature version: {manifest.get('feature_version')}")
    if manifest.get("label_version") != LABEL_VERSION:
        raise ValueError(f"Unexpected label version: {manifest.get('label_version')}")
    feature_hashes = manifest.get("feature_hashes", {})
    label_hashes = manifest.get("label_hashes", {})
    for date in expected_dates:
        if not feature_hashes.get(date):
            raise ValueError(f"Missing feature hash for {date}")
        if not label_hashes.get(date):
            raise ValueError(f"Missing label hash for {date}")
    return {
        "snapshot_hash": recorded_hash,
        "computed_snapshot_hash": computed_hash,
        "included_date_count": len(included_dates),
        "first_date": included_dates[0],
        "last_date": included_dates[-1],
    }


def pairwise_valid(x: pd.Series, y: pd.Series) -> pd.DataFrame:
    paired = pd.DataFrame(
        {
            "feature": pd.to_numeric(x, errors="coerce"),
            "label": pd.to_numeric(y, errors="coerce"),
        }
    )
    return paired.dropna()


def spearman_ic(x: pd.Series, y: pd.Series) -> float:
    x_values = pd.to_numeric(x, errors="coerce").to_numpy(dtype=float)
    y_values = pd.to_numeric(y, errors="coerce").to_numpy(dtype=float)
    valid = np.isfinite(x_values) & np.isfinite(y_values)
    if int(valid.sum()) < 2:
        return math.nan
    x_valid = x_values[valid]
    y_valid = y_values[valid]
    if len(np.unique(x_valid)) < 2 or len(np.unique(y_valid)) < 2:
        return math.nan
    feature_rank = stats.rankdata(x_valid, method="average")
    label_rank = stats.rankdata(y_valid, method="average")
    return float(np.corrcoef(feature_rank, label_rank)[0, 1])


def pearson_ic(x: pd.Series, y: pd.Series) -> float:
    x_values = pd.to_numeric(x, errors="coerce").to_numpy(dtype=float)
    y_values = pd.to_numeric(y, errors="coerce").to_numpy(dtype=float)
    valid = np.isfinite(x_values) & np.isfinite(y_values)
    if int(valid.sum()) < 2:
        return math.nan
    x_valid = x_values[valid]
    y_valid = y_values[valid]
    if len(np.unique(x_valid)) < 2 or len(np.unique(y_valid)) < 2:
        return math.nan
    return float(np.corrcoef(x_valid, y_valid)[0, 1])


def count_valid_rows(feature: pd.Series, label: pd.Series) -> dict[str, int | float]:
    numeric_feature = pd.to_numeric(feature, errors="coerce")
    numeric_label = pd.to_numeric(label, errors="coerce")
    feature_valid = numeric_feature.notna()
    label_valid = numeric_label.notna()
    paired = feature_valid & label_valid
    candidate_rows = int(len(feature))
    paired_rows = int(paired.sum())
    return {
        "candidate_rows": candidate_rows,
        "valid_feature_rows": int(feature_valid.sum()),
        "valid_label_rows": int(label_valid.sum()),
        "valid_paired_rows": paired_rows,
        "paired_coverage": paired_rows / candidate_rows if candidate_rows else math.nan,
    }


def nonoverlap_mask(row_count: int, stride: int) -> np.ndarray:
    if stride < 1:
        raise ValueError("Non-overlap stride must be positive")
    return (np.arange(row_count) % stride) == 0


def assign_decile_buckets(feature: pd.Series, bucket_count: int = 10) -> pd.Series:
    numeric = pd.to_numeric(feature, errors="coerce")
    buckets = pd.Series(pd.NA, index=feature.index, dtype="Int64")
    valid = numeric.dropna()
    n = len(valid)
    if n == 0:
        return buckets
    ranks = valid.rank(method="first", ascending=True)
    bucket_values = np.floor((ranks.to_numpy(dtype=float) - 1.0) * bucket_count / n).astype(int) + 1
    bucket_values = np.clip(bucket_values, 1, bucket_count)
    buckets.loc[valid.index] = bucket_values
    return buckets


def bucket_mean_rows(
    *,
    date: str,
    feature_name: str,
    horizon: str,
    feature: pd.Series,
    returns: pd.Series,
    move_bps: pd.Series,
    bucket_count: int = 10,
) -> list[dict[str, Any]]:
    feature_numeric = pd.to_numeric(feature, errors="coerce")
    return_numeric = pd.to_numeric(returns, errors="coerce")
    move_numeric = pd.to_numeric(move_bps, errors="coerce")
    valid = feature_numeric.notna() & return_numeric.notna() & move_numeric.notna()
    buckets = assign_decile_buckets(feature_numeric[valid], bucket_count=bucket_count)
    frame = pd.DataFrame(
        {
            "bucket": buckets.astype("Int64"),
            "return": return_numeric[valid],
            "move_bps": move_numeric[valid],
        }
    ).dropna(subset=["bucket"])
    rows: list[dict[str, Any]] = []
    for bucket in range(1, bucket_count + 1):
        bucket_frame = frame[frame["bucket"] == bucket]
        rows.append(
            {
                "date": date,
                "feature": feature_name,
                "horizon": horizon,
                "bucket": bucket,
                "mean_future_return": _finite_mean(bucket_frame["return"]),
                "mean_future_move_bps": _finite_mean(bucket_frame["move_bps"]),
                "median_future_move_bps": _finite_median(bucket_frame["move_bps"]),
                "observation_count": int(len(bucket_frame)),
            }
        )
    return rows


def top_bottom_effect(rows: Iterable[dict[str, Any]]) -> float:
    rows_by_bucket = {
        int(row["bucket"]): row for row in rows if row.get("observation_count", 0) > 0
    }
    if not rows_by_bucket:
        return math.nan
    low = min(rows_by_bucket)
    high = max(rows_by_bucket)
    high_value = rows_by_bucket[high]["mean_future_move_bps"]
    low_value = rows_by_bucket[low]["mean_future_move_bps"]
    if pd.isna(high_value) or pd.isna(low_value):
        return math.nan
    return float(high_value - low_value)


def bucket_monotonicity(rows: Iterable[dict[str, Any]]) -> float:
    frame = pd.DataFrame(list(rows))
    frame = frame.dropna(subset=["mean_future_return"])
    frame = frame[frame["observation_count"] > 0]
    if len(frame) < 2:
        return math.nan
    return spearman_ic(frame["bucket"], frame["mean_future_return"])


def next_move_bucket_rows(
    *,
    date: str,
    feature_name: str,
    feature: pd.Series,
    available: pd.Series,
    direction: pd.Series,
    bucket_count: int = 10,
) -> list[dict[str, Any]]:
    feature_numeric = pd.to_numeric(feature, errors="coerce")
    availability = available.astype(str).str.lower().eq("true")
    normalized_direction = direction.astype(str).str.strip()
    valid = feature_numeric.notna() & availability & normalized_direction.isin({"1", "-1"})
    buckets = assign_decile_buckets(feature_numeric[valid], bucket_count=bucket_count)
    frame = pd.DataFrame(
        {"bucket": buckets.astype("Int64"), "direction": normalized_direction[valid]}
    ).dropna(subset=["bucket"])
    rows: list[dict[str, Any]] = []
    for bucket in range(1, bucket_count + 1):
        bucket_frame = frame[frame["bucket"] == bucket]
        count = int(len(bucket_frame))
        up = int((bucket_frame["direction"] == "1").sum())
        down = int((bucket_frame["direction"] == "-1").sum())
        rows.append(
            {
                "date": date,
                "feature": feature_name,
                "feature_group": (
                    "primary" if feature_name in NEXT_MOVE_PRIMARY_FEATURES else "secondary"
                ),
                "bucket": bucket,
                "p_up": up / count if count else math.nan,
                "p_down": down / count if count else math.nan,
                "count": count,
                "p_up_minus_down": (up - down) / count if count else math.nan,
            }
        )
    return rows


def direction_bucket_rows(
    *,
    date: str,
    feature_name: str,
    horizon: str,
    feature: pd.Series,
    direction: pd.Series,
    bucket_count: int = 10,
) -> list[dict[str, Any]]:
    feature_numeric = pd.to_numeric(feature, errors="coerce")
    normalized_direction = direction.astype(str).str.strip()
    valid = feature_numeric.notna() & normalized_direction.isin({"UP", "DOWN", "FLAT"})
    buckets = assign_decile_buckets(feature_numeric[valid], bucket_count=bucket_count)
    frame = pd.DataFrame(
        {"bucket": buckets.astype("Int64"), "direction": normalized_direction[valid]}
    ).dropna(subset=["bucket"])
    rows: list[dict[str, Any]] = []
    for bucket in range(1, bucket_count + 1):
        bucket_frame = frame[frame["bucket"] == bucket]
        count = int(len(bucket_frame))
        up = int((bucket_frame["direction"] == "UP").sum())
        down = int((bucket_frame["direction"] == "DOWN").sum())
        flat = int((bucket_frame["direction"] == "FLAT").sum())
        rows.append(
            {
                "date": date,
                "feature": feature_name,
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


def summarize_daily_values(values: Iterable[float]) -> dict[str, Any]:
    finite = pd.Series(list(values), dtype="float64").replace([np.inf, -np.inf], np.nan).dropna()
    valid_days = int(len(finite))
    if valid_days == 0:
        return {
            "valid_days": 0,
            "mean": math.nan,
            "median": math.nan,
            "std": math.nan,
            "min": math.nan,
            "max": math.nan,
            "t_stat": math.nan,
            "raw_p_value": math.nan,
            "positive_days": 0,
            "negative_days": 0,
            "zero_days": 0,
            "sign_consistency": math.nan,
            "sign_test_p_value": math.nan,
        }
    mean = float(finite.mean())
    std = float(finite.std(ddof=1)) if valid_days > 1 else math.nan
    t_stat = mean / (std / math.sqrt(valid_days)) if valid_days > 1 and std > 0 else math.nan
    raw_p = (
        float(stats.t.sf(abs(t_stat), df=valid_days - 1) * 2)
        if math.isfinite(t_stat)
        else math.nan
    )
    positive = int((finite > 0).sum())
    negative = int((finite < 0).sum())
    zero = int((finite == 0).sum())
    directional = positive + negative
    sign_p = (
        float(stats.binomtest(positive, n=directional, p=0.5, alternative="two-sided").pvalue)
        if directional
        else math.nan
    )
    return {
        "valid_days": valid_days,
        "mean": mean,
        "median": float(finite.median()),
        "std": std,
        "min": float(finite.min()),
        "max": float(finite.max()),
        "t_stat": t_stat,
        "raw_p_value": raw_p,
        "positive_days": positive,
        "negative_days": negative,
        "zero_days": zero,
        "sign_consistency": positive / directional if directional else math.nan,
        "sign_test_p_value": sign_p,
    }


def benjamini_hochberg(p_values: Iterable[float]) -> list[float]:
    values = list(p_values)
    finite_pairs = [(index, value) for index, value in enumerate(values) if math.isfinite(value)]
    q_values = [math.nan] * len(values)
    if not finite_pairs:
        return q_values
    ranked = sorted(finite_pairs, key=lambda pair: pair[1])
    m = len(ranked)
    previous = 1.0
    for rank, (index, value) in reversed(list(enumerate(ranked, start=1))):
        adjusted = min(previous, value * m / rank)
        previous = adjusted
        q_values[index] = float(min(adjusted, 1.0))
    return q_values


def stable_seed(seed: int, key: str) -> int:
    return int(hash_config({"seed": seed, "key": key})[:8], 16)


def deterministic_permutation(values: pd.Series, *, seed: int, key: str) -> pd.Series:
    rng = np.random.default_rng(stable_seed(seed, key))
    numeric = pd.to_numeric(values, errors="coerce")
    valid_index = numeric.dropna().index
    shuffled = numeric.copy()
    shuffled.loc[valid_index] = rng.permutation(numeric.loc[valid_index].to_numpy())
    return shuffled


def split_year(date: str) -> str:
    year = date[:4]
    if year not in {"2024", "2025"}:
        raise ValueError(f"Phase 7 only permits 2024/2025 development dates, got {date}")
    return year


def deterministic_results_hash(output_dir: str | Path) -> str:
    directory = Path(output_dir)
    filenames = [
        "primary_ic.csv",
        "daily_ic.csv",
        "nonoverlap_ic.csv",
        "bucket_results.csv",
        "next_move_results.csv",
        "direction_results.csv",
        "phase7_summary.json",
    ]
    payload: dict[str, Any] = {}
    for filename in filenames:
        path = directory / filename
        content = path.read_text(encoding="utf-8")
        if filename == "phase7_summary.json":
            summary = json.loads(content)
            summary.pop("phase7_results_hash", None)
            payload[filename] = summary
        else:
            payload[filename] = content
    return hash_config(payload)


def ensure_no_zero_fill(before: pd.Series, after: pd.Series) -> None:
    before_na = pd.to_numeric(before, errors="coerce").isna()
    if (before_na & pd.to_numeric(after, errors="coerce").eq(0)).any():
        raise ValueError("Missing alpha features were filled with zero")


def _finite_mean(series: pd.Series) -> float:
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    return float(numeric.mean()) if len(numeric) else math.nan


def _finite_median(series: pd.Series) -> float:
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    return float(numeric.median()) if len(numeric) else math.nan
