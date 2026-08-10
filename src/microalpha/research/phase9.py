"""Phase 9 walk-forward temporal robustness helpers."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from microalpha.pipeline.registry import DEVELOPMENT_DATES
from microalpha.research.phase8 import (
    ANCHOR_OFFSET,
    ANCHOR_STRIDE,
    SEED,
    anchor_mask,
    pearson_array,
    regression_metrics,
    spearman_array,
    validate_feature_columns,
)
from microalpha.research.phase8 import (
    FEATURE_SETS as PHASE8_FEATURE_SETS,
)
from microalpha.utils.hashing import hash_config

PHASE7_SNAPSHOT_HASH = "0bcdb7eddebbe83458998eff78844471afb78fc66d249a53aeb25667bebd803a"
PHASE8_MODELING_PLAN_HASH = "823ee7a98be9a5199842536a65edd2394a39f1c5c6ae13947c29bd7c1c2494fe"
PHASE8_RESULTS_HASH = "d8471add338d79106fb1839008c5168535bb644505b5282a5e6236147b31255d"
PHASE8_ARTIFACT_COMMIT = "0cff6ce05980ac226ec47f0d602045a6dadf9993"
PHASE9_PLAN_PATH = Path("data/manifests/phase9_walkforward_plan.yaml")
PHASE9_PLAN_HASH = "4b1f0f0dd9f638ff4b5f40af04e17e8fc7753c4a500cac650d2537d3d40fb2c4"
MIN_TRAINING_DATES = 6
NEGATIVE_CONTROL_FOLDS = (1, 9, 18)

WALKFORWARD_FEATURE_SETS = {
    "qi_only": list(PHASE8_FEATURE_SETS["qi_only"]),
    "qi_ofi": list(PHASE8_FEATURE_SETS["qi_ofi"]),
    "extended_book_flow": list(PHASE8_FEATURE_SETS["extended_book_flow"]),
}

LIGHTGBM_PARAMS = {
    "n_estimators": 120,
    "learning_rate": 0.05,
    "num_leaves": 15,
    "max_depth": 4,
    "min_child_samples": 200,
    "subsample": 0.8,
    "colsample_bytree": 0.9,
    "reg_alpha": 0.1,
    "reg_lambda": 1.0,
    "random_state": SEED,
    "deterministic": True,
    "force_col_wise": True,
    "n_jobs": 1,
    "verbosity": -1,
}

PRIMARY_MODELS = {
    "qi_direct_baseline": "qi_only",
    "lightgbm_qi_ofi": "qi_ofi",
    "lightgbm_extended": "extended_book_flow",
}

FORBIDDEN_MODEL_COLUMNS = {
    "next_mid_change_available",
    "next_mid_change_direction",
    "time_to_next_mid_change_ms",
}
FORBIDDEN_MODEL_PREFIXES = (
    "ret_fwd_",
    "direction_",
    "future_mid_move_",
    "future_move_in_spreads_",
    "target_time_",
    "actual_label_time_",
    "label_delay_ms_",
)


@dataclass(frozen=True)
class WalkForwardFold:
    fold_id: int
    window: str
    train_dates: tuple[str, ...]
    validation_date: str


def validate_development_dates(dates: tuple[str, ...] = DEVELOPMENT_DATES) -> None:
    if dates != tuple(sorted(dates)):
        raise ValueError("Development dates must be chronological")
    if len(dates) != 24:
        raise ValueError("Phase 9 expects exactly 24 development dates")
    if any(str(date).startswith("2026-") for date in dates):
        raise ValueError("Phase 9 must not access 2026 holdout dates")
    if any(not (str(date).startswith("2024-") or str(date).startswith("2025-")) for date in dates):
        raise ValueError("Phase 9 may use only 2024-2025 development dates")


def build_expanding_folds(
    dates: tuple[str, ...] = DEVELOPMENT_DATES,
    *,
    minimum_training_dates: int = MIN_TRAINING_DATES,
) -> list[WalkForwardFold]:
    validate_development_dates(dates)
    folds = []
    for index in range(minimum_training_dates, len(dates)):
        folds.append(
            WalkForwardFold(
                fold_id=len(folds) + 1,
                window="expanding",
                train_dates=dates[:index],
                validation_date=dates[index],
            )
        )
    validate_folds(folds, expected_count=18, window="expanding")
    return folds


def build_rolling6_folds(dates: tuple[str, ...] = DEVELOPMENT_DATES) -> list[WalkForwardFold]:
    validate_development_dates(dates)
    folds = []
    for index in range(MIN_TRAINING_DATES, len(dates)):
        folds.append(
            WalkForwardFold(
                fold_id=len(folds) + 1,
                window="rolling6",
                train_dates=dates[index - MIN_TRAINING_DATES : index],
                validation_date=dates[index],
            )
        )
    validate_folds(folds, expected_count=18, window="rolling6")
    return folds


def validate_folds(
    folds: list[WalkForwardFold], *, expected_count: int | None = None, window: str | None = None
) -> None:
    if expected_count is not None and len(folds) != expected_count:
        raise ValueError(f"Expected {expected_count} folds, got {len(folds)}")
    previous_validation = ""
    for fold in folds:
        if window is not None and fold.window != window:
            raise ValueError(f"Unexpected fold window: {fold.window}")
        if fold.validation_date.startswith("2026-"):
            raise ValueError("Phase 9 must not access 2026 holdout dates")
        if any(train_date >= fold.validation_date for train_date in fold.train_dates):
            raise ValueError("Every training date must strictly precede validation date")
        if previous_validation and fold.validation_date <= previous_validation:
            raise ValueError("Validation folds must be chronological")
        previous_validation = fold.validation_date


def validate_walkforward_feature_sets() -> None:
    expected = {
        "qi_only": ["qi_1"],
        "qi_ofi": ["qi_1", "ofi_1s"],
        "extended_book_flow": [
            "qi_1",
            "di_5",
            "di_10",
            "ofi_1s",
            "trade_imbalance_1s",
            "spread_bps",
            "realized_vol_5s",
            "mom_1s",
            "book_update_count_1s",
            "trade_count_1s",
        ],
    }
    if WALKFORWARD_FEATURE_SETS != expected:
        raise ValueError("Phase 9 feature sets must remain frozen from Phase 8")
    for features in WALKFORWARD_FEATURE_SETS.values():
        validate_model_columns(features)


def validate_lightgbm_params(params: dict[str, Any] = LIGHTGBM_PARAMS) -> None:
    expected = {
        "n_estimators": 120,
        "learning_rate": 0.05,
        "num_leaves": 15,
        "max_depth": 4,
        "min_child_samples": 200,
        "subsample": 0.8,
        "colsample_bytree": 0.9,
        "reg_alpha": 0.1,
        "reg_lambda": 1.0,
        "random_state": SEED,
        "deterministic": True,
        "force_col_wise": True,
        "n_jobs": 1,
        "verbosity": -1,
    }
    if params != expected:
        raise ValueError("Phase 9 LightGBM parameters must remain frozen from Phase 8")


def validate_model_columns(columns: list[str]) -> None:
    validate_feature_columns(columns)
    for column in columns:
        if column in FORBIDDEN_MODEL_COLUMNS or column.startswith(FORBIDDEN_MODEL_PREFIXES):
            raise ValueError(f"Future/target-derived column cannot enter model matrix: {column}")


def anchored_mask(row_count: int) -> np.ndarray:
    return anchor_mask(row_count, stride=ANCHOR_STRIDE, offset=ANCHOR_OFFSET)


def fold_delta_rows(metrics: pd.DataFrame, *, window: str) -> pd.DataFrame:
    rows = []
    subset = metrics[metrics["window"] == window]
    for validation_date, group in subset.groupby("validation_date", sort=True):
        by_model = group.set_index("model")
        qi = float(by_model.loc["qi_direct_baseline", "spearman_ic"])
        qi_ofi = float(by_model.loc["lightgbm_qi_ofi", "spearman_ic"])
        extended = float(by_model.loc["lightgbm_extended", "spearman_ic"])
        rows.append(
            {
                "window": window,
                "validation_date": validation_date,
                "delta_ic_qi_ofi": qi_ofi - qi,
                "delta_ic_extended": extended - qi,
                "ofi_increment": qi_ofi - qi,
                "extended_increment": extended - qi_ofi,
            }
        )
    return pd.DataFrame(rows)


def sign_test(values: pd.Series) -> dict[str, Any]:
    finite = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    positives = int((finite > 0).sum())
    negatives = int((finite < 0).sum())
    zeros = int((finite == 0).sum())
    trials = positives + negatives
    p_value = math.nan
    if trials:
        p_value = float(stats.binomtest(positives, trials, p=0.5, alternative="two-sided").pvalue)
    return {
        "positive_folds": positives,
        "negative_folds": negatives,
        "zero_folds": zeros,
        "sign_test_trials": trials,
        "sign_test_p_value": p_value,
    }


def t_stat(values: pd.Series) -> float:
    finite = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if len(finite) < 2:
        return math.nan
    std = float(finite.std(ddof=1))
    if std == 0:
        mean = float(finite.mean())
        if mean > 0:
            return math.inf
        if mean < 0:
            return -math.inf
        return 0.0
    return float(finite.mean() / (std / math.sqrt(len(finite))))


def delta_summary(values: pd.Series) -> dict[str, Any]:
    finite = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    signs = sign_test(finite)
    return {
        "mean_delta_ic": float(finite.mean()) if len(finite) else math.nan,
        "median_delta_ic": float(finite.median()) if len(finite) else math.nan,
        "std_delta_ic": float(finite.std(ddof=1)) if len(finite) > 1 else math.nan,
        "min_delta_ic": float(finite.min()) if len(finite) else math.nan,
        "max_delta_ic": float(finite.max()) if len(finite) else math.nan,
        "fold_t_stat": t_stat(finite),
        **signs,
    }


def period_label(date: str) -> str:
    year = int(date[:4])
    month = int(date[5:7])
    if year == 2024 and month >= 7:
        return "2024_H2"
    if year == 2025 and month <= 6:
        return "2025_H1"
    if year == 2025 and month >= 7:
        return "2025_H2"
    return "unused"


def model_regression_row(
    *,
    fold: WalkForwardFold,
    model: str,
    feature_set: str,
    y_true: np.ndarray,
    y_pred: np.ndarray,
    train_row_count: int,
) -> dict[str, Any]:
    return {
        "window": fold.window,
        "fold_id": fold.fold_id,
        "validation_date": fold.validation_date,
        "period": period_label(fold.validation_date),
        "model": model,
        "feature_set": feature_set,
        "train_start_date": fold.train_dates[0],
        "train_end_date": fold.train_dates[-1],
        "train_date_count": len(fold.train_dates),
        "train_row_count": train_row_count,
        **regression_metrics(y_true, y_pred),
    }


def prediction_rank_corr(x: np.ndarray, y: np.ndarray) -> float:
    return spearman_array(np.asarray(x, dtype=float), np.asarray(y, dtype=float))


def pearson_ic(x: np.ndarray, y: np.ndarray) -> float:
    return pearson_array(np.asarray(x, dtype=float), np.asarray(y, dtype=float))


def deterministic_results_hash(output_dir: str | Path) -> str:
    directory = Path(output_dir)
    files = [
        "walkforward_metrics.csv",
        "incremental_lift.csv",
        "window_comparison.csv",
        "prediction_correlations.csv",
        "feature_importance_by_fold.csv",
        "negative_control.csv",
        "phase9_summary.json",
        "README.md",
    ]
    payload: dict[str, Any] = {}
    for file in files:
        content = (directory / file).read_text(encoding="utf-8")
        if file == "phase9_summary.json":
            summary = json.loads(content)
            summary.pop("phase9_results_hash", None)
            payload[file] = summary
        else:
            payload[file] = content
    return hash_config(payload)
