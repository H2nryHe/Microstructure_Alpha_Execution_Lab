"""Run Phase 9 walk-forward temporal robustness on development dates only."""

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
from sklearn.linear_model import LinearRegression

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from microalpha.config import load_yaml_config
from microalpha.research.phase7 import FEATURE_VERSION, LABEL_VERSION, load_snapshot_manifest
from microalpha.research.phase8 import (
    PHASE7_AUDIT_HASH,
    PHASE7_RESULTS_HASH,
    SEED,
    TARGET,
    TrainOnlyPreprocessor,
)
from microalpha.research.phase9 import (
    LIGHTGBM_PARAMS,
    NEGATIVE_CONTROL_FOLDS,
    PHASE7_SNAPSHOT_HASH,
    PHASE8_ARTIFACT_COMMIT,
    PHASE8_MODELING_PLAN_HASH,
    PHASE8_RESULTS_HASH,
    PHASE9_PLAN_HASH,
    PRIMARY_MODELS,
    WALKFORWARD_FEATURE_SETS,
    WalkForwardFold,
    anchored_mask,
    build_expanding_folds,
    build_rolling6_folds,
    delta_summary,
    deterministic_results_hash,
    fold_delta_rows,
    model_regression_row,
    pearson_ic,
    period_label,
    prediction_rank_corr,
    validate_development_dates,
    validate_folds,
    validate_lightgbm_params,
    validate_model_columns,
    validate_walkforward_feature_sets,
)
from microalpha.utils.hashing import hash_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--derived-root", default="/tmp/microalpha-multiday/derived")
    parser.add_argument("--snapshot", default="data/manifests/pre_phase7_research_snapshot.json")
    parser.add_argument("--plan", default="data/manifests/phase9_walkforward_plan.yaml")
    parser.add_argument("--phase8-summary", default="reports/phase8/phase8_summary.json")
    parser.add_argument("--output-dir", default="reports/phase9")
    parser.add_argument("--clean", action="store_true")
    return parser.parse_args()


def feature_path(root: Path, date: str) -> Path:
    return root / f"date={date}" / f"features_{FEATURE_VERSION}.parquet"


def label_path(root: Path, date: str) -> Path:
    return root / f"date={date}" / f"labels_{LABEL_VERSION}.parquet"


def required_feature_columns() -> list[str]:
    columns = {"observation_time"}
    for features in WALKFORWARD_FEATURE_SETS.values():
        columns.update(features)
    return sorted(columns)


def required_label_columns() -> list[str]:
    return ["observation_time", TARGET]


def read_model_frame(root: Path, date: str) -> pd.DataFrame:
    if date.startswith("2026-"):
        raise ValueError("Phase 9 must not load 2026 holdout dates")
    features = pd.read_parquet(feature_path(root, date), columns=required_feature_columns())
    labels = pd.read_parquet(label_path(root, date), columns=required_label_columns())
    if not features["observation_time"].reset_index(drop=True).equals(
        labels["observation_time"].reset_index(drop=True)
    ):
        raise ValueError(f"Feature/label timestamp mismatch for {date}")
    mask = anchored_mask(len(features))
    frame = pd.concat(
        [
            features.loc[mask].reset_index(drop=True),
            labels.loc[mask].drop(columns=["observation_time"]).reset_index(drop=True),
        ],
        axis=1,
    )
    frame.insert(0, "date", date)
    for column in sorted(set(required_feature_columns()) - {"observation_time"} | {TARGET}):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("float64")
    return frame


def try_lightgbm() -> tuple[Any | None, str]:
    try:
        import lightgbm as lgb
    except Exception as exc:  # noqa: BLE001 - dependency state is reported.
        return None, f"{type(exc).__name__}: {exc}"
    return lgb, ""


def verify_inputs(args: argparse.Namespace) -> dict[str, Any]:
    snapshot = load_snapshot_manifest(args.snapshot)
    if snapshot.get("snapshot_hash") != PHASE7_SNAPSHOT_HASH:
        raise ValueError("Unexpected Phase 7 snapshot hash")
    if any(str(date).startswith("2026-") for date in snapshot.get("included_dates", [])):
        raise ValueError("Snapshot includes 2026 holdout dates")
    plan_hash = hash_config(load_yaml_config(args.plan))
    if plan_hash != PHASE9_PLAN_HASH:
        raise ValueError(f"Phase 9 plan hash mismatch: {plan_hash}")
    phase8_summary = json.loads(Path(args.phase8_summary).read_text(encoding="utf-8"))
    if phase8_summary.get("phase8_modeling_plan_hash") != PHASE8_MODELING_PLAN_HASH:
        raise ValueError("Unexpected Phase 8 modeling plan hash")
    if phase8_summary.get("phase8_results_hash") != PHASE8_RESULTS_HASH:
        raise ValueError("Unexpected Phase 8 results hash")
    if phase8_summary.get("phase7_results_hash") != PHASE7_RESULTS_HASH:
        raise ValueError("Unexpected Phase 7 result hash")
    if phase8_summary.get("phase7_audit_hash") != PHASE7_AUDIT_HASH:
        raise ValueError("Unexpected Phase 7 audit hash")
    validate_development_dates()
    validate_walkforward_feature_sets()
    validate_lightgbm_params()
    return {"phase9_plan_hash": plan_hash}


def load_all_frames(root: Path, dates: tuple[str, ...]) -> dict[str, pd.DataFrame]:
    return {date: read_model_frame(root, date) for date in dates}


def concat_dates(frames: dict[str, pd.DataFrame], dates: tuple[str, ...]) -> pd.DataFrame:
    return pd.concat([frames[date] for date in dates], ignore_index=True)


def fit_predict_fold(
    *,
    fold: WalkForwardFold,
    train: pd.DataFrame,
    validation: pd.DataFrame,
    lgb_module: Any,
) -> tuple[list[dict[str, Any]], dict[str, np.ndarray], list[dict[str, Any]]]:
    train_valid = train.dropna(subset=[TARGET]).copy()
    val_valid = validation.dropna(subset=[TARGET]).copy()
    metrics_rows: list[dict[str, Any]] = []
    predictions: dict[str, np.ndarray] = {}
    importance_rows: list[dict[str, Any]] = []

    for model, feature_set in PRIMARY_MODELS.items():
        features = WALKFORWARD_FEATURE_SETS[feature_set]
        validate_model_columns(features)
        if model == "qi_direct_baseline":
            pre = TrainOnlyPreprocessor().fit(train_valid, features)
            fitted = LinearRegression().fit(pre.transform(train_valid), train_valid[TARGET])
            prediction = fitted.predict(pre.transform(val_valid))
        else:
            fitted = lgb_module.LGBMRegressor(**LIGHTGBM_PARAMS).fit(
                train_valid[features], train_valid[TARGET]
            )
            prediction = fitted.predict(val_valid[features])
            importance_rows.extend(lightgbm_importance_rows(fold, model, feature_set, fitted))
        predictions[model] = np.asarray(prediction, dtype=float)
        metrics_rows.append(
            model_regression_row(
                fold=fold,
                model=model,
                feature_set=feature_set,
                y_true=val_valid[TARGET].to_numpy(dtype=float),
                y_pred=prediction,
                train_row_count=len(train_valid),
            )
        )
    return metrics_rows, predictions, importance_rows


def lightgbm_importance_rows(
    fold: WalkForwardFold, model: str, feature_set: str, fitted: Any
) -> list[dict[str, Any]]:
    rows = []
    features = WALKFORWARD_FEATURE_SETS[feature_set]
    for feature, gain, split in zip(
        features,
        fitted.booster_.feature_importance(importance_type="gain"),
        fitted.booster_.feature_importance(importance_type="split"),
        strict=True,
    ):
        common = {
            "window": fold.window,
            "fold_id": fold.fold_id,
            "validation_date": fold.validation_date,
            "model": model,
            "feature_set": feature_set,
            "feature": feature,
        }
        rows.append({**common, "importance_type": "gain", "importance": float(gain)})
        rows.append({**common, "importance_type": "split", "importance": float(split)})
    return rows


def prediction_correlation_rows(
    *,
    fold: WalkForwardFold,
    validation: pd.DataFrame,
    predictions: dict[str, np.ndarray],
) -> list[dict[str, Any]]:
    val_valid = validation.dropna(subset=[TARGET]).copy()
    qi = val_valid["qi_1"].to_numpy(dtype=float)
    rows = [
        {
            "window": fold.window,
            "fold_id": fold.fold_id,
            "validation_date": fold.validation_date,
            "comparison": "lightgbm_qi_ofi_prediction_vs_qi_1",
            "spearman_rank_correlation": prediction_rank_corr(predictions["lightgbm_qi_ofi"], qi),
        },
        {
            "window": fold.window,
            "fold_id": fold.fold_id,
            "validation_date": fold.validation_date,
            "comparison": "lightgbm_extended_prediction_vs_qi_1",
            "spearman_rank_correlation": prediction_rank_corr(predictions["lightgbm_extended"], qi),
        },
        {
            "window": fold.window,
            "fold_id": fold.fold_id,
            "validation_date": fold.validation_date,
            "comparison": "lightgbm_extended_prediction_vs_lightgbm_qi_ofi_prediction",
            "spearman_rank_correlation": prediction_rank_corr(
                predictions["lightgbm_extended"], predictions["lightgbm_qi_ofi"]
            ),
        },
    ]
    return rows


def run_window(
    *,
    frames: dict[str, pd.DataFrame],
    folds: list[WalkForwardFold],
    lgb_module: Any,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    validate_folds(folds, expected_count=18)
    metrics_rows: list[dict[str, Any]] = []
    corr_rows: list[dict[str, Any]] = []
    importance_rows: list[dict[str, Any]] = []
    for fold in folds:
        print(f"phase9 {fold.window} fold {fold.fold_id} validate {fold.validation_date}")
        train = concat_dates(frames, fold.train_dates)
        validation = frames[fold.validation_date]
        fold_metrics, predictions, fold_importance = fit_predict_fold(
            fold=fold, train=train, validation=validation, lgb_module=lgb_module
        )
        metrics_rows.extend(fold_metrics)
        corr_rows.extend(
            prediction_correlation_rows(
                fold=fold, validation=validation, predictions=predictions
            )
        )
        importance_rows.extend(fold_importance)
    return (
        pd.DataFrame(metrics_rows),
        pd.DataFrame(corr_rows),
        pd.DataFrame(importance_rows),
    )


def incremental_lift(metrics: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for window in ["expanding", "rolling6"]:
        fold_rows = fold_delta_rows(metrics, window=window)
        fold_rows["cumulative_mean_delta_qi_ofi"] = (
            fold_rows["delta_ic_qi_ofi"].expanding().mean()
        )
        fold_rows["cumulative_mean_delta_extended"] = (
            fold_rows["delta_ic_extended"].expanding().mean()
        )
        rows.append(fold_rows)
    return pd.concat(rows, ignore_index=True)


def window_comparison(lift: pd.DataFrame) -> pd.DataFrame:
    expanding = lift[lift["window"] == "expanding"].set_index("validation_date")
    rolling = lift[lift["window"] == "rolling6"].set_index("validation_date")
    rows = []
    for date in expanding.index:
        rows.append(
            {
                "validation_date": date,
                "delta_ic_qi_ofi_expanding": expanding.loc[date, "delta_ic_qi_ofi"],
                "delta_ic_qi_ofi_rolling6": rolling.loc[date, "delta_ic_qi_ofi"],
                "delta_ic_extended_expanding": expanding.loc[date, "delta_ic_extended"],
                "delta_ic_extended_rolling6": rolling.loc[date, "delta_ic_extended"],
                "extended_increment_expanding": expanding.loc[date, "extended_increment"],
                "extended_increment_rolling6": rolling.loc[date, "extended_increment"],
            }
        )
    return pd.DataFrame(rows)


def negative_control(
    *,
    frames: dict[str, pd.DataFrame],
    folds: list[WalkForwardFold],
    lgb_module: Any,
) -> pd.DataFrame:
    rows = []
    selected = [fold for fold in folds if fold.fold_id in NEGATIVE_CONTROL_FOLDS]
    rng = np.random.default_rng(SEED)
    features = WALKFORWARD_FEATURE_SETS["qi_ofi"]
    for fold in selected:
        train = concat_dates(frames, fold.train_dates).dropna(subset=[TARGET]).copy()
        validation = frames[fold.validation_date].dropna(subset=[TARGET]).copy()
        permuted = train[TARGET].to_numpy(dtype=float).copy()
        valid = np.isfinite(permuted)
        permuted[valid] = rng.permutation(permuted[valid])
        fitted = lgb_module.LGBMRegressor(**LIGHTGBM_PARAMS).fit(train[features], permuted)
        prediction = fitted.predict(validation[features])
        spearman = prediction_rank_corr(prediction, validation[TARGET].to_numpy())
        prediction_std = float(np.std(prediction))
        target_unique_count = int(pd.Series(validation[TARGET]).nunique(dropna=True))
        effective_spearman = spearman
        status = "valid_rank_metric"
        if not np.isfinite(spearman):
            effective_spearman = 0.0
            status = "no_rank_signal_constant_prediction"
        rows.append(
            {
                "control": "deterministic_permuted_train_target_walkforward",
                "window": "expanding",
                "fold_id": fold.fold_id,
                "validation_date": fold.validation_date,
                "model": "lightgbm_qi_ofi",
                "feature_set": "qi_ofi",
                "spearman_ic": spearman,
                "effective_spearman_ic": effective_spearman,
                "pearson_ic": pearson_ic(prediction, validation[TARGET].to_numpy()),
                "prediction_std": prediction_std,
                "target_unique_count": target_unique_count,
                "status": status,
                "row_count": len(validation),
            }
        )
    return pd.DataFrame(rows)


def summarize_outputs(
    *,
    metrics: pd.DataFrame,
    lift: pd.DataFrame,
    comparison: pd.DataFrame,
    correlations: pd.DataFrame,
    negative: pd.DataFrame,
    input_info: dict[str, Any],
    lgb_available: bool,
) -> dict[str, Any]:
    expanding = metrics[metrics["window"] == "expanding"]
    qi = expanding[expanding["model"] == "qi_direct_baseline"]["spearman_ic"]
    expanding_lift = lift[lift["window"] == "expanding"]
    rolling_lift = lift[lift["window"] == "rolling6"]
    period_rows = period_summaries(metrics, lift)
    negative_mean = finite_mean(negative["effective_spearman_ic"])
    phase9_status = "PASS"
    if len(expanding_lift) != 18:
        phase9_status = "FAIL"
    if abs(negative_mean) > 0.05:
        phase9_status = "FAIL"
    return {
        "phase": "Phase 9 - Walk-Forward Temporal Robustness",
        "phase9_status": phase9_status,
        "phase9_plan_hash": input_info["phase9_plan_hash"],
        "phase9_results_hash": "",
        "phase7_snapshot_hash": PHASE7_SNAPSHOT_HASH,
        "phase7_results_hash": PHASE7_RESULTS_HASH,
        "phase7_audit_hash": PHASE7_AUDIT_HASH,
        "phase8_modeling_plan_hash": PHASE8_MODELING_PLAN_HASH,
        "phase8_results_hash": PHASE8_RESULTS_HASH,
        "phase8_artifact_commit": PHASE8_ARTIFACT_COMMIT,
        "phase8_ci": {
            "tests": {
                "run_id": 31351466257,
                "conclusion": "success",
                "head_sha": PHASE8_ARTIFACT_COMMIT,
            },
            "research_smoke": {
                "run_id": 31351466255,
                "conclusion": "success",
                "head_sha": PHASE8_ARTIFACT_COMMIT,
            },
        },
        "holdout_accessed": False,
        "development_dates": list(frames_development_dates()),
        "expanding_fold_count": int((metrics["window"] == "expanding").sum() / 3),
        "rolling6_fold_count": int((metrics["window"] == "rolling6").sum() / 3),
        "qi_ic": summarize_series(qi),
        "expanding_delta_qi_ofi": {
            **delta_summary(expanding_lift["delta_ic_qi_ofi"]),
        },
        "expanding_delta_extended": {
            **delta_summary(expanding_lift["delta_ic_extended"]),
        },
        "expanding_extended_increment": {
            **delta_summary(expanding_lift["extended_increment"]),
        },
        "rolling_delta_qi_ofi": {
            **delta_summary(rolling_lift["delta_ic_qi_ofi"]),
        },
        "rolling_delta_extended": {
            **delta_summary(rolling_lift["delta_ic_extended"]),
        },
        "window_comparison_mean": {
            "delta_ic_qi_ofi_expanding": finite_mean(
                comparison["delta_ic_qi_ofi_expanding"]
            ),
            "delta_ic_qi_ofi_rolling6": finite_mean(
                comparison["delta_ic_qi_ofi_rolling6"]
            ),
            "delta_ic_extended_expanding": finite_mean(
                comparison["delta_ic_extended_expanding"]
            ),
            "delta_ic_extended_rolling6": finite_mean(
                comparison["delta_ic_extended_rolling6"]
            ),
        },
        "period_summaries": period_rows,
        "prediction_similarity": correlation_summaries(correlations),
        "negative_control": negative.to_dict(orient="records"),
        "negative_control_mean_effective_spearman_ic": negative_mean,
        "lightgbm_available_locally": lgb_available,
        "limitations": [
            "Phase 9 used only 2024-2025 development dates.",
            "2026 holdout was not accessed.",
            (
                "Phase 9 is predictive temporal robustness only and makes no "
                "trading or economic claim."
            ),
            "No model or threshold selection rule was changed from Phase 8.",
        ],
    }


def frames_development_dates() -> tuple[str, ...]:
    from microalpha.pipeline.registry import DEVELOPMENT_DATES

    return DEVELOPMENT_DATES


def summarize_series(values: pd.Series) -> dict[str, Any]:
    finite = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    return {
        "mean": float(finite.mean()) if len(finite) else math.nan,
        "median": float(finite.median()) if len(finite) else math.nan,
        "std": float(finite.std(ddof=1)) if len(finite) > 1 else math.nan,
        "min": float(finite.min()) if len(finite) else math.nan,
        "max": float(finite.max()) if len(finite) else math.nan,
        "positive_count": int((finite > 0).sum()),
        "negative_count": int((finite < 0).sum()),
    }


def finite_mean(values: pd.Series) -> float:
    finite = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    return float(finite.mean()) if len(finite) else math.nan


def period_summaries(metrics: pd.DataFrame, lift: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    metric_period = metrics[metrics["window"] == "expanding"].copy()
    for (period, model), group in metric_period.groupby(["period", "model"], sort=True):
        rows.append(
            {
                "period": period,
                "model": model,
                "mean_ic": finite_mean(group["spearman_ic"]),
                "median_ic": float(group["spearman_ic"].median()),
            }
        )
    for period, group in lift[lift["window"] == "expanding"].groupby(
        lift["validation_date"].map(period_label), sort=True
    ):
        for column in ["delta_ic_qi_ofi", "delta_ic_extended"]:
            rows.append(
                {
                    "period": period,
                    "model": column,
                    "mean_ic": finite_mean(group[column]),
                    "median_ic": float(group[column].median()),
                    "positive_lift_fraction": float((group[column] > 0).mean()),
                }
            )
    return rows


def correlation_summaries(correlations: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for (window, comparison), group in correlations.groupby(["window", "comparison"], sort=True):
        rows.append(
            {
                "window": window,
                "comparison": comparison,
                "mean_rank_correlation": finite_mean(group["spearman_rank_correlation"]),
                "median_rank_correlation": float(group["spearman_rank_correlation"].median()),
                "min_rank_correlation": float(group["spearman_rank_correlation"].min()),
                "max_rank_correlation": float(group["spearman_rank_correlation"].max()),
            }
        )
    return rows


def write_outputs(
    *,
    output_dir: Path,
    metrics: pd.DataFrame,
    lift: pd.DataFrame,
    comparison: pd.DataFrame,
    correlations: pd.DataFrame,
    importance: pd.DataFrame,
    negative: pd.DataFrame,
    summary: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics.to_csv(output_dir / "walkforward_metrics.csv", index=False, float_format="%.12g")
    lift.to_csv(output_dir / "incremental_lift.csv", index=False, float_format="%.12g")
    comparison.to_csv(output_dir / "window_comparison.csv", index=False, float_format="%.12g")
    correlations.to_csv(
        output_dir / "prediction_correlations.csv", index=False, float_format="%.12g"
    )
    importance.to_csv(
        output_dir / "feature_importance_by_fold.csv", index=False, float_format="%.12g"
    )
    negative.to_csv(output_dir / "negative_control.csv", index=False, float_format="%.12g")
    (output_dir / "README.md").write_text(readme_text(summary), encoding="utf-8")
    summary = sanitize_json(summary)
    (output_dir / "phase9_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    result_hash = deterministic_results_hash(output_dir)
    summary["phase9_results_hash"] = result_hash
    (output_dir / "phase9_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    plot_outputs(output_dir, metrics, lift, comparison, correlations, importance)


def readme_text(summary: dict[str, Any]) -> str:
    return f"""# Phase 9 Walk-Forward Temporal Robustness

Status: {summary["phase9_status"]}

Plan hash: `{summary["phase9_plan_hash"]}`

Result hash is recorded in `phase9_summary.json`.

Phase 9 uses only 2024-2025 development dates with deterministic 1s anchors.
The 2026 holdout was not accessed.

Primary comparison: QI direct baseline vs QI+OFI LightGBM vs Extended LightGBM.
"""


def sanitize_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: sanitize_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_json(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def plot_outputs(
    output_dir: Path,
    metrics: pd.DataFrame,
    lift: pd.DataFrame,
    comparison: pd.DataFrame,
    correlations: pd.DataFrame,
    importance: pd.DataFrame,
) -> None:
    figures = output_dir / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    expanding = metrics[metrics["window"] == "expanding"]
    plot_ic_by_date(expanding, figures / "walkforward_ic_by_validation_date.png")
    plot_delta_by_date(lift[lift["window"] == "expanding"], figures / "delta_ic_vs_qi_by_date.png")
    plot_cumulative_delta(
        lift[lift["window"] == "expanding"], figures / "cumulative_average_delta_ic.png"
    )
    plot_window_comparison(comparison, figures / "expanding_vs_rolling6_delta_comparison.png")
    plot_single_delta(
        lift[lift["window"] == "expanding"],
        "ofi_increment",
        figures / "qi_ofi_incremental_contribution_by_date.png",
        "QI+OFI Increment",
    )
    plot_single_delta(
        lift[lift["window"] == "expanding"],
        "extended_increment",
        figures / "extended_beyond_qi_ofi_contribution_by_date.png",
        "Extended Beyond QI+OFI",
    )
    plot_prediction_corr(
        correlations[correlations["window"] == "expanding"],
        figures / "prediction_qi_rank_correlation_by_fold.png",
    )
    plot_importance_stability(
        importance[importance["window"] == "expanding"],
        figures / "feature_importance_stability.png",
    )


def plot_ic_by_date(frame: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 4))
    dates = sorted(frame["validation_date"].unique())
    x = np.arange(len(dates))
    for model in PRIMARY_MODELS:
        subset = frame[frame["model"] == model].set_index("validation_date").loc[dates]
        ax.plot(x, subset["spearman_ic"], marker="o", label=model)
    ax.set_xticks(x, dates, rotation=45, ha="right")
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_title("Walk-Forward IC by Validation Date")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_delta_by_date(frame: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 4))
    x = np.arange(len(frame))
    ax.plot(x, frame["delta_ic_qi_ofi"], marker="o", label="QI+OFI - QI")
    ax.plot(x, frame["delta_ic_extended"], marker="o", label="Extended - QI")
    ax.set_xticks(x, frame["validation_date"], rotation=45, ha="right")
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_title("Delta IC vs QI")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_cumulative_delta(frame: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(9, 4))
    x = np.arange(len(frame))
    ax.plot(x, frame["cumulative_mean_delta_qi_ofi"], marker="o", label="QI+OFI")
    ax.plot(x, frame["cumulative_mean_delta_extended"], marker="o", label="Extended")
    ax.set_xticks(x, frame["validation_date"], rotation=45, ha="right")
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_title("Cumulative Average Delta IC")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_window_comparison(frame: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.scatter(frame["delta_ic_extended_expanding"], frame["delta_ic_extended_rolling6"])
    lim = max(
        abs(frame["delta_ic_extended_expanding"]).max(),
        abs(frame["delta_ic_extended_rolling6"]).max(),
    )
    ax.plot([-lim, lim], [-lim, lim], color="black", linewidth=0.8)
    ax.axhline(0, color="black", linewidth=0.5)
    ax.axvline(0, color="black", linewidth=0.5)
    ax.set_xlabel("Expanding Extended Delta IC")
    ax.set_ylabel("Rolling-6 Extended Delta IC")
    ax.set_title("Expanding vs Rolling-6 Delta")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_single_delta(frame: pd.DataFrame, column: str, path: Path, title: str) -> None:
    fig, ax = plt.subplots(figsize=(10, 4))
    x = np.arange(len(frame))
    ax.bar(x, frame[column])
    ax.set_xticks(x, frame["validation_date"], rotation=45, ha="right")
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_prediction_corr(frame: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 4))
    dates = sorted(frame["validation_date"].unique())
    x = np.arange(len(dates))
    for comparison in sorted(frame["comparison"].unique()):
        subset = frame[frame["comparison"] == comparison].set_index("validation_date").loc[dates]
        ax.plot(x, subset["spearman_rank_correlation"], marker="o", label=comparison)
    ax.set_xticks(x, dates, rotation=45, ha="right")
    ax.set_title("Prediction Rank Correlation")
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_importance_stability(frame: pd.DataFrame, path: Path) -> None:
    subset = frame[
        (frame["model"] == "lightgbm_extended") & (frame["importance_type"] == "gain")
    ].copy()
    fig, ax = plt.subplots(figsize=(8, 4))
    if subset.empty:
        ax.text(0.5, 0.5, "No importance data", ha="center", va="center")
        ax.set_axis_off()
    else:
        pivot = subset.pivot_table(
            index="feature", values="importance", aggfunc=["mean", "std"]
        ).fillna(0)
        pivot.columns = ["mean_importance", "std_importance"]
        pivot = pivot.sort_values("mean_importance", ascending=False)
        x = np.arange(len(pivot))
        ax.bar(x, pivot["mean_importance"], yerr=pivot["std_importance"])
        ax.set_xticks(x, pivot.index, rotation=45, ha="right")
        ax.set_title("Extended LightGBM Gain Importance Stability")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    if args.clean and output_dir.exists():
        shutil.rmtree(output_dir)
    input_info = verify_inputs(args)
    lgb_module, lgb_error = try_lightgbm()
    if lgb_module is None:
        raise RuntimeError(f"LightGBM is required for Phase 9: {lgb_error}")
    root = Path(args.derived_root)
    dates = frames_development_dates()
    frames = load_all_frames(root, dates)
    expanding_folds = build_expanding_folds(dates)
    rolling_folds = build_rolling6_folds(dates)
    expanding_metrics, expanding_corr, expanding_importance = run_window(
        frames=frames, folds=expanding_folds, lgb_module=lgb_module
    )
    rolling_metrics, rolling_corr, rolling_importance = run_window(
        frames=frames, folds=rolling_folds, lgb_module=lgb_module
    )
    metrics = pd.concat([expanding_metrics, rolling_metrics], ignore_index=True)
    correlations = pd.concat([expanding_corr, rolling_corr], ignore_index=True)
    importance = pd.concat([expanding_importance, rolling_importance], ignore_index=True)
    lift = incremental_lift(metrics)
    comparison = window_comparison(lift)
    negative = negative_control(
        frames=frames, folds=expanding_folds, lgb_module=lgb_module
    )
    summary = summarize_outputs(
        metrics=metrics,
        lift=lift,
        comparison=comparison,
        correlations=correlations,
        negative=negative,
        input_info=input_info,
        lgb_available=True,
    )
    write_outputs(
        output_dir=output_dir,
        metrics=metrics,
        lift=lift,
        comparison=comparison,
        correlations=correlations,
        importance=importance,
        negative=negative,
        summary=summary,
    )
    final_summary = json.loads((output_dir / "phase9_summary.json").read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "phase9_results_hash": final_summary["phase9_results_hash"],
                "phase9_status": final_summary["phase9_status"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
