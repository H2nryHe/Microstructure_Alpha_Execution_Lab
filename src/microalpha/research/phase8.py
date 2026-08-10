"""Phase 8 baseline predictive modeling helpers."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.metrics import (
    brier_score_loss,
    log_loss,
    mean_absolute_error,
    mean_squared_error,
    r2_score,
    roc_auc_score,
)
from sklearn.preprocessing import StandardScaler

from microalpha.pipeline.registry import DEVELOPMENT_DATES
from microalpha.research.phase7 import spearman_ic, summarize_daily_values
from microalpha.utils.hashing import hash_config

PHASE7_RESULTS_HASH = "b86d51c4317f87d0cabf579f152d07c139e7fc23e47356d655bd09057342eb04"
PHASE7_AUDIT_HASH = "b6b8206e03c81b47787d5ae4d4e5b960b4748bc75eed0ee5be4862ebf190d6e1"
PHASE7_AUDIT_COMMIT = "d785b28907776865ebd1ca799cfd6ad1611e3717"
TRAIN_DATES = tuple(date for date in DEVELOPMENT_DATES if date.startswith("2024-"))
VALIDATION_DATES = tuple(date for date in DEVELOPMENT_DATES if date.startswith("2025-"))
TARGET = "ret_fwd_1s"
CLASSIFICATION_TARGET = "next_mid_change_direction"
ANCHOR_STRIDE = 10
ANCHOR_OFFSET = 0
SEED = 8008

FEATURE_SETS = {
    "qi_only": ["qi_1"],
    "qi_ofi": ["qi_1", "ofi_1s"],
    "qi_trade_imbalance": ["qi_1", "trade_imbalance_1s"],
    "core_independent_microstructure": ["qi_1", "ofi_1s", "trade_imbalance_1s"],
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
    "reference_ofi_only": ["ofi_1s"],
    "reference_trade_imbalance_only": ["trade_imbalance_1s"],
}

FORBIDDEN_FEATURE_PREFIXES = (
    "ret_fwd_",
    "direction_",
    "future_mid_move_",
    "future_move_in_spreads_",
    "target_time_",
    "actual_label_time_",
    "label_delay_ms_",
)
FORBIDDEN_FEATURE_COLUMNS = {
    "next_mid_change_direction",
    "time_to_next_mid_change_ms",
}


class TrainOnlyPreprocessor:
    """Median-impute and standardize using training data only."""

    def __init__(self) -> None:
        self.features: list[str] = []
        self.output_features: list[str] = []
        self.medians: dict[str, float] = {}
        self.missing_indicator_features: list[str] = []
        self.scaler = StandardScaler()

    def fit(self, frame: pd.DataFrame, features: list[str]) -> "TrainOnlyPreprocessor":
        validate_feature_columns(features)
        self.features = list(features)
        transformed = self._with_missing_indicators(frame[self.features], fit=True)
        self.output_features = list(transformed.columns)
        self.scaler.fit(transformed)
        return self

    def transform(self, frame: pd.DataFrame) -> np.ndarray:
        transformed = self._with_missing_indicators(frame[self.features], fit=False)
        return self.scaler.transform(transformed[self.output_features])

    def _with_missing_indicators(self, frame: pd.DataFrame, *, fit: bool) -> pd.DataFrame:
        output = frame.copy()
        for column in self.features:
            numeric = pd.to_numeric(output[column], errors="coerce")
            if fit:
                median = float(numeric.median()) if numeric.notna().any() else 0.0
                self.medians[column] = median
                if numeric.isna().any():
                    self.missing_indicator_features.append(column)
            output[column] = numeric.fillna(self.medians[column])
        for column in self.missing_indicator_features:
            output[f"{column}_missing"] = (
                pd.to_numeric(frame[column], errors="coerce").isna().astype(float)
            )
        return output


def validate_feature_columns(features: list[str]) -> None:
    for column in features:
        if column in FORBIDDEN_FEATURE_COLUMNS or column.startswith(FORBIDDEN_FEATURE_PREFIXES):
            raise ValueError(f"Future/target-derived column cannot be a feature: {column}")


def validate_dates(train_dates: list[str], validation_dates: list[str]) -> None:
    if train_dates != sorted(train_dates) or validation_dates != sorted(validation_dates):
        raise ValueError("Train and validation dates must be chronological")
    if any(date.startswith("2026-") for date in [*train_dates, *validation_dates]):
        raise ValueError("Phase 8 must not load 2026 holdout dates")
    if any(not date.startswith("2024-") for date in train_dates):
        raise ValueError("Training dates must be 2024 development dates")
    if any(not date.startswith("2025-") for date in validation_dates):
        raise ValueError("Validation dates must be 2025 development dates")


def anchor_mask(
    row_count: int, *, stride: int = ANCHOR_STRIDE, offset: int = ANCHOR_OFFSET
) -> np.ndarray:
    if stride <= 0:
        raise ValueError("Anchor stride must be positive")
    if not 0 <= offset < stride:
        raise ValueError("Anchor offset must be in [0, stride)")
    return (np.arange(row_count) % stride) == offset


def regression_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    valid = np.isfinite(y_true) & np.isfinite(y_pred)
    y = y_true[valid]
    pred = y_pred[valid]
    nonzero = y != 0
    return {
        "spearman_ic": spearman_array(pred, y),
        "pearson_corr": pearson_array(pred, y),
        "mae": float(mean_absolute_error(y, pred)),
        "rmse": float(math.sqrt(mean_squared_error(y, pred))),
        "r2": float(r2_score(y, pred)),
        "sign_accuracy_nonzero": float((np.sign(pred[nonzero]) == np.sign(y[nonzero])).mean())
        if int(nonzero.sum())
        else math.nan,
        "prediction_mean": float(pred.mean()),
        "prediction_std": float(pred.std(ddof=0)),
        "target_mean": float(y.mean()),
        "target_std": float(y.std(ddof=0)),
        "row_count": int(len(y)),
    }


def classification_metrics(y_true: np.ndarray, prob: np.ndarray) -> dict[str, float]:
    valid = np.isfinite(y_true) & np.isfinite(prob)
    y = y_true[valid].astype(int)
    p = np.clip(prob[valid], 1e-15, 1 - 1e-15)
    return {
        "roc_auc": float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else math.nan,
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "brier_score": float(brier_score_loss(y, p)),
        "row_count": int(len(y)),
        "positive_rate": float(y.mean()) if len(y) else math.nan,
        "prediction_mean": float(p.mean()) if len(p) else math.nan,
    }


def daily_ic(frame: pd.DataFrame, prediction_col: str, target_col: str = TARGET) -> pd.DataFrame:
    rows = []
    for date, group in frame.groupby("date", sort=True):
        rows.append(
            {
                "date": date,
                "daily_spearman_ic": spearman_ic(group[prediction_col], group[target_col]),
                "row_count": int(group[[prediction_col, target_col]].dropna().shape[0]),
            }
        )
    return pd.DataFrame(rows)


def lift_summary(model_daily: pd.Series, baseline_daily: pd.Series) -> dict[str, Any]:
    delta = model_daily.to_numpy(dtype=float) - baseline_daily.to_numpy(dtype=float)
    finite = pd.Series(delta).replace([np.inf, -np.inf], np.nan).dropna()
    return {
        "mean_delta_daily_ic": float(finite.mean()) if len(finite) else math.nan,
        "median_delta_daily_ic": float(finite.median()) if len(finite) else math.nan,
        "min_delta_daily_ic": float(finite.min()) if len(finite) else math.nan,
        "max_delta_daily_ic": float(finite.max()) if len(finite) else math.nan,
        "positive_lift_days": int((finite > 0).sum()),
        "negative_lift_days": int((finite < 0).sum()),
    }


def spearman_array(x: np.ndarray, y: np.ndarray) -> float:
    valid = np.isfinite(x) & np.isfinite(y)
    if int(valid.sum()) < 2:
        return math.nan
    xv = x[valid]
    yv = y[valid]
    if len(np.unique(xv)) < 2 or len(np.unique(yv)) < 2:
        return math.nan
    return float(np.corrcoef(stats.rankdata(xv), stats.rankdata(yv))[0, 1])


def pearson_array(x: np.ndarray, y: np.ndarray) -> float:
    valid = np.isfinite(x) & np.isfinite(y)
    if int(valid.sum()) < 2:
        return math.nan
    return float(np.corrcoef(x[valid], y[valid])[0, 1])


def deterministic_permutation(values: np.ndarray, *, seed: int = SEED) -> np.ndarray:
    rng = np.random.default_rng(seed)
    output = values.copy()
    valid = np.isfinite(output)
    output[valid] = rng.permutation(output[valid])
    return output


def deterministic_results_hash(output_dir: str | Path) -> str:
    directory = Path(output_dir)
    files = [
        "regression_results.csv",
        "daily_regression_metrics.csv",
        "ablation_results.csv",
        "prediction_correlations.csv",
        "feature_importance.csv",
        "classification_results.csv",
        "classification_calibration.csv",
        "negative_control.csv",
        "phase8_summary.json",
        "README.md",
    ]
    payload: dict[str, Any] = {}
    for file in files:
        content = (directory / file).read_text(encoding="utf-8")
        if file == "phase8_summary.json":
            summary = json.loads(content)
            summary.pop("phase8_results_hash", None)
            payload[file] = summary
        else:
            payload[file] = content
    return hash_config(payload)


def daily_summary(values: pd.Series) -> dict[str, Any]:
    return summarize_daily_values(values)
