"""Run Phase 8 baseline predictive modeling on the frozen development split."""

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
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LinearRegression, LogisticRegression, Ridge

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from microalpha.config import load_yaml_config
from microalpha.research.phase7 import FEATURE_VERSION, LABEL_VERSION, load_snapshot_manifest
from microalpha.research.phase8 import (
    CLASSIFICATION_TARGET,
    FEATURE_SETS,
    PHASE7_AUDIT_COMMIT,
    PHASE7_AUDIT_HASH,
    PHASE7_RESULTS_HASH,
    SEED,
    TARGET,
    TRAIN_DATES,
    VALIDATION_DATES,
    TrainOnlyPreprocessor,
    anchor_mask,
    classification_metrics,
    daily_ic,
    daily_summary,
    deterministic_permutation,
    deterministic_results_hash,
    lift_summary,
    regression_metrics,
    spearman_ic,
    validate_dates,
    validate_feature_columns,
)
from microalpha.utils.hashing import hash_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--derived-root", default="/tmp/microalpha-multiday/derived")
    parser.add_argument("--snapshot", default="data/manifests/pre_phase7_research_snapshot.json")
    parser.add_argument("--plan", default="data/manifests/phase8_modeling_plan.yaml")
    parser.add_argument("--phase7-summary", default="reports/phase7/phase7_summary.json")
    parser.add_argument("--phase7-audit-summary", default="reports/phase7/audit/audit_summary.json")
    parser.add_argument("--output-dir", default="reports/phase8")
    parser.add_argument("--clean", action="store_true")
    return parser.parse_args()


def feature_path(root: Path, date: str) -> Path:
    return root / f"date={date}" / f"features_{FEATURE_VERSION}.parquet"


def label_path(root: Path, date: str) -> Path:
    return root / f"date={date}" / f"labels_{LABEL_VERSION}.parquet"


def required_feature_columns() -> list[str]:
    columns = {"observation_time"}
    for features in FEATURE_SETS.values():
        columns.update(features)
    columns.update({"qi_1", "ofi_1s", "trade_imbalance_1s"})
    return sorted(columns)


def required_label_columns() -> list[str]:
    return [
        "observation_time",
        TARGET,
        "next_mid_change_available",
        CLASSIFICATION_TARGET,
    ]


def read_model_frame(root: Path, dates: tuple[str, ...], split: str) -> pd.DataFrame:
    frames = []
    for date in dates:
        if date.startswith("2026-"):
            raise ValueError("Phase 8 must not load 2026 holdout dates")
        features = pd.read_parquet(feature_path(root, date), columns=required_feature_columns())
        labels = pd.read_parquet(label_path(root, date), columns=required_label_columns())
        if (
            not features["observation_time"]
            .reset_index(drop=True)
            .equals(labels["observation_time"].reset_index(drop=True))
        ):
            raise ValueError(f"Feature/label timestamp mismatch for {date}")
        mask = anchor_mask(len(features))
        frame = pd.concat(
            [
                features.loc[mask].reset_index(drop=True),
                labels.loc[mask].drop(columns=["observation_time"]).reset_index(drop=True),
            ],
            axis=1,
        )
        frame.insert(0, "date", date)
        frame.insert(1, "split", split)
        frames.append(frame)
    output = pd.concat(frames, ignore_index=True)
    numeric_columns = set(required_feature_columns()) - {"observation_time"}
    numeric_columns.add(TARGET)
    for column in sorted(numeric_columns & set(output.columns)):
        output[column] = pd.to_numeric(output[column], errors="coerce").astype("float64")
    return output


def try_lightgbm() -> tuple[Any | None, str]:
    try:
        import lightgbm as lgb
    except Exception as exc:  # noqa: BLE001 - local dependency state is reported.
        return None, f"{type(exc).__name__}: {exc}"
    return lgb, ""


def fit_predict_regression(
    *,
    model_name: str,
    feature_set: str,
    train: pd.DataFrame,
    validation: pd.DataFrame,
    lgb_module: Any | None,
    lgb_error: str,
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame, list[dict[str, Any]]]:
    features = FEATURE_SETS[feature_set]
    validate_feature_columns(features)
    train_valid = train.dropna(subset=[TARGET])
    validation_valid = validation.dropna(subset=[TARGET])
    importance_rows: list[dict[str, Any]] = []
    status = "fit"
    skip_reason = ""

    if model_name == "null_mean":
        prediction = np.full(len(validation_valid), train_valid[TARGET].mean())
        fitted = None
        output_features = []
    elif model_name == "qi_linear":
        pre = TrainOnlyPreprocessor().fit(train_valid, features)
        x_train = pre.transform(train_valid)
        x_val = pre.transform(validation_valid)
        fitted = LinearRegression().fit(x_train, train_valid[TARGET].to_numpy())
        prediction = fitted.predict(x_val)
        output_features = pre.output_features
    elif model_name == "ridge":
        pre = TrainOnlyPreprocessor().fit(train_valid, features)
        x_train = pre.transform(train_valid)
        x_val = pre.transform(validation_valid)
        fitted = Ridge(alpha=1.0).fit(x_train, train_valid[TARGET].to_numpy())
        prediction = fitted.predict(x_val)
        output_features = pre.output_features
    elif model_name == "lightgbm_regression":
        if lgb_module is None:
            prediction = np.full(len(validation_valid), math.nan)
            fitted = None
            output_features = []
            status = "skipped"
            skip_reason = f"LightGBM unavailable locally: {lgb_error}"
        else:
            params = {
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
            x_train = train_valid[features]
            x_val = validation_valid[features]
            fitted = lgb_module.LGBMRegressor(**params).fit(x_train, train_valid[TARGET])
            prediction = fitted.predict(x_val)
            output_features = features
            for feature, gain, split in zip(
                features,
                fitted.booster_.feature_importance(importance_type="gain"),
                fitted.booster_.feature_importance(importance_type="split"),
                strict=True,
            ):
                importance_rows.append(
                    {
                        "model": model_name,
                        "feature_set": feature_set,
                        "feature": feature,
                        "importance_type": "lightgbm_gain",
                        "importance": float(gain),
                    }
                )
                importance_rows.append(
                    {
                        "model": model_name,
                        "feature_set": feature_set,
                        "feature": feature,
                        "importance_type": "lightgbm_split",
                        "importance": float(split),
                    }
                )
            if len(validation_valid) > 0:
                sample = validation_valid.head(min(100000, len(validation_valid)))
                perm = permutation_importance(
                    fitted,
                    sample[features],
                    sample[TARGET],
                    n_repeats=3,
                    random_state=SEED,
                    n_jobs=1,
                    scoring="neg_mean_squared_error",
                )
                for feature, value in zip(features, perm.importances_mean, strict=True):
                    importance_rows.append(
                        {
                            "model": model_name,
                            "feature_set": feature_set,
                            "feature": feature,
                            "importance_type": "permutation_neg_mse",
                            "importance": float(value),
                        }
                    )
    else:
        raise ValueError(f"Unsupported regression model: {model_name}")

    pred_col = f"pred_{model_name}_{feature_set}"
    predictions = validation_valid[["date", TARGET, "qi_1", "ofi_1s", "trade_imbalance_1s"]].copy()
    predictions[pred_col] = prediction
    if status == "skipped":
        metrics = empty_regression_row(model_name, feature_set, status, skip_reason)
        daily = empty_daily_rows(
            model_name, feature_set, validation_valid["date"].unique(), skip_reason
        )
        return metrics, daily, predictions, importance_rows

    metrics = {
        "model": model_name,
        "feature_set": feature_set,
        "status": status,
        "skip_reason": skip_reason,
        **regression_metrics(validation_valid[TARGET].to_numpy(dtype=float), prediction),
        "feature_count": len(features),
        "output_feature_count": len(output_features),
    }
    daily = daily_ic(predictions, pred_col).rename(columns={"daily_spearman_ic": "spearman_ic"})
    daily.insert(0, "model", model_name)
    daily.insert(1, "feature_set", feature_set)
    return metrics, daily, predictions, importance_rows


def empty_regression_row(model: str, feature_set: str, status: str, reason: str) -> dict[str, Any]:
    return {
        "model": model,
        "feature_set": feature_set,
        "status": status,
        "skip_reason": reason,
        "spearman_ic": math.nan,
        "pearson_corr": math.nan,
        "mae": math.nan,
        "rmse": math.nan,
        "r2": math.nan,
        "sign_accuracy_nonzero": math.nan,
        "prediction_mean": math.nan,
        "prediction_std": math.nan,
        "target_mean": math.nan,
        "target_std": math.nan,
        "row_count": 0,
        "feature_count": len(FEATURE_SETS[feature_set]),
        "output_feature_count": 0,
    }


def empty_daily_rows(model: str, feature_set: str, dates: np.ndarray, reason: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "model": model,
                "feature_set": feature_set,
                "date": date,
                "spearman_ic": math.nan,
                "row_count": 0,
                "skip_reason": reason,
            }
            for date in sorted(dates)
        ]
    )


def regression_plan() -> list[tuple[str, str]]:
    pairs = [("null_mean", "qi_only"), ("qi_linear", "qi_only")]
    for feature_set in [
        "qi_only",
        "qi_ofi",
        "qi_trade_imbalance",
        "core_independent_microstructure",
        "extended_book_flow",
        "reference_ofi_only",
        "reference_trade_imbalance_only",
    ]:
        pairs.append(("ridge", feature_set))
        pairs.append(("lightgbm_regression", feature_set))
    return pairs


def add_daily_summaries(regression: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, row in regression.iterrows():
        subset = daily[
            (daily["model"] == row["model"]) & (daily["feature_set"] == row["feature_set"])
        ]
        stats = daily_summary(subset["spearman_ic"])
        rows.append(
            {
                **row.to_dict(),
                "mean_daily_spearman_ic": stats["mean"],
                "median_daily_spearman_ic": stats["median"],
                "std_daily_spearman_ic": stats["std"],
                "min_daily_spearman_ic": stats["min"],
                "max_daily_spearman_ic": stats["max"],
                "positive_day_count": stats["positive_days"],
            }
        )
    return pd.DataFrame(rows)


def ablations(regression: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    baseline = daily[(daily["model"] == "qi_linear") & (daily["feature_set"] == "qi_only")]
    baseline = baseline.sort_values("date")["spearman_ic"].reset_index(drop=True)
    rows = []
    for keys, group in daily.groupby(["model", "feature_set"], sort=True):
        model, feature_set = keys
        values = group.sort_values("date")["spearman_ic"].reset_index(drop=True)
        lift = lift_summary(values, baseline)
        rows.append({"model": model, "feature_set": feature_set, **lift})
    return pd.DataFrame(rows)


def prediction_correlations(prediction_frames: list[pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for frame in prediction_frames:
        pred_cols = [column for column in frame.columns if column.startswith("pred_")]
        for pred_col in pred_cols:
            model, feature_set = parse_prediction_column(pred_col)
            for feature in ["qi_1", "ofi_1s", "trade_imbalance_1s"]:
                rows.append(
                    {
                        "prediction_column": pred_col,
                        "model": model,
                        "feature_set": feature_set,
                        "reference_feature": feature,
                        "spearman_rank_correlation": spearman_ic(frame[pred_col], frame[feature]),
                    }
                )
    return pd.DataFrame(rows)


def parse_prediction_column(column: str) -> tuple[str, str]:
    payload = column.removeprefix("pred_")
    for feature_set in sorted(FEATURE_SETS, key=len, reverse=True):
        suffix = f"_{feature_set}"
        if payload.endswith(suffix):
            return payload[: -len(suffix)], feature_set
    return payload, ""


def classification_run(
    train: pd.DataFrame,
    validation: pd.DataFrame,
    lgb_module: Any | None,
    lgb_error: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    train_class = class_frame(train)
    validation_class = class_frame(validation)
    rows = []
    calibration_rows = []
    models = [
        ("logistic_qi", "qi_only"),
        ("logistic_core", "core_independent_microstructure"),
        ("lightgbm_classifier", "core_independent_microstructure"),
    ]
    baseline_metrics: dict[str, float] | None = None
    for model, feature_set in models:
        features = FEATURE_SETS[feature_set]
        if model.startswith("logistic"):
            pre = TrainOnlyPreprocessor().fit(train_class, features)
            fitted = LogisticRegression(
                C=1.0,
                penalty="l2",
                solver="lbfgs",
                max_iter=1000,
                random_state=SEED,
            ).fit(pre.transform(train_class), train_class["class_target"])
            prob = fitted.predict_proba(pre.transform(validation_class))[:, 1]
            status = "fit"
            reason = ""
        elif lgb_module is None:
            prob = np.full(len(validation_class), math.nan)
            status = "skipped"
            reason = f"LightGBM unavailable locally: {lgb_error}"
        else:
            fitted = lgb_module.LGBMClassifier(
                n_estimators=120,
                learning_rate=0.05,
                num_leaves=15,
                max_depth=4,
                min_child_samples=200,
                subsample=0.8,
                colsample_bytree=0.9,
                reg_alpha=0.1,
                reg_lambda=1.0,
                random_state=SEED,
                deterministic=True,
                force_col_wise=True,
                n_jobs=1,
                verbosity=-1,
            ).fit(train_class[features], train_class["class_target"])
            prob = fitted.predict_proba(validation_class[features])[:, 1]
            status = "fit"
            reason = ""
        if status == "fit":
            metrics = classification_metrics(validation_class["class_target"].to_numpy(), prob)
            if model == "logistic_qi":
                baseline_metrics = metrics
            delta = {}
            if baseline_metrics is not None:
                delta = {
                    "delta_auc_vs_qi": metrics["roc_auc"] - baseline_metrics["roc_auc"],
                    "delta_log_loss_vs_qi": metrics["log_loss"] - baseline_metrics["log_loss"],
                    "delta_brier_vs_qi": metrics["brier_score"] - baseline_metrics["brier_score"],
                }
            calibration_rows.extend(
                calibration(model, feature_set, validation_class["class_target"], prob)
            )
        else:
            metrics = {
                "roc_auc": math.nan,
                "log_loss": math.nan,
                "brier_score": math.nan,
                "row_count": 0,
                "positive_rate": math.nan,
                "prediction_mean": math.nan,
            }
            delta = {
                "delta_auc_vs_qi": math.nan,
                "delta_log_loss_vs_qi": math.nan,
                "delta_brier_vs_qi": math.nan,
            }
        rows.append(
            {
                "model": model,
                "feature_set": feature_set,
                "status": status,
                "skip_reason": reason,
                **metrics,
                **delta,
            }
        )
    return pd.DataFrame(rows), pd.DataFrame(calibration_rows)


def class_frame(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame[
        frame["next_mid_change_available"].astype(str).str.lower().eq("true")
        & frame[CLASSIFICATION_TARGET].astype(str).isin(["1", "-1"])
    ].copy()
    output["class_target"] = output[CLASSIFICATION_TARGET].astype(str).map({"-1": 0, "1": 1})
    for features in FEATURE_SETS.values():
        for feature in features:
            output[feature] = pd.to_numeric(output[feature], errors="coerce")
    return output.dropna(subset=["class_target"])


def calibration(
    model: str, feature_set: str, y: pd.Series, prob: np.ndarray
) -> list[dict[str, Any]]:
    frame = pd.DataFrame({"target": y.to_numpy(dtype=int), "prob": prob})
    frame = frame[np.isfinite(frame["prob"])].copy()
    frame = frame.sort_values(["prob"], kind="mergesort")
    if frame.empty:
        return []
    frame["decile"] = (np.floor(np.arange(len(frame)) * 10 / len(frame)).astype(int) + 1).clip(
        1, 10
    )
    rows = []
    for decile, group in frame.groupby("decile", sort=True):
        rows.append(
            {
                "model": model,
                "feature_set": feature_set,
                "decile": int(decile),
                "mean_predicted_probability": float(group["prob"].mean()),
                "observed_up_rate": float(group["target"].mean()),
                "count": int(len(group)),
            }
        )
    return rows


def negative_control(train: pd.DataFrame, validation: pd.DataFrame) -> pd.DataFrame:
    features = FEATURE_SETS["core_independent_microstructure"]
    train_pool = train.dropna(subset=[TARGET]).copy()
    validation_pool = validation.dropna(subset=[TARGET]).copy()
    rng = np.random.default_rng(SEED)
    train_indices = rng.choice(
        train_pool.index.to_numpy(),
        size=min(200000, len(train_pool)),
        replace=False,
    )
    validation_indices = rng.choice(
        validation_pool.index.to_numpy(),
        size=min(200000, len(validation_pool)),
        replace=False,
    )
    train_sample = train_pool.loc[np.sort(train_indices)].copy()
    validation_sample = validation_pool.loc[np.sort(validation_indices)].copy()
    y_perm = deterministic_permutation(train_sample[TARGET].to_numpy(dtype=float))
    y_perm_validation = deterministic_permutation(validation_sample[TARGET].to_numpy(dtype=float))
    pre = TrainOnlyPreprocessor().fit(train_sample, features)
    fitted = Ridge(alpha=1.0).fit(pre.transform(train_sample), y_perm)
    pred = fitted.predict(pre.transform(validation_sample))
    metrics = regression_metrics(y_perm_validation, pred)
    metrics["real_target_spearman_ic_diagnostic"] = spearman_ic(
        pd.Series(pred),
        validation_sample[TARGET].reset_index(drop=True),
    )
    return pd.DataFrame(
        [
            {
                "control": "deterministic_permuted_train_target",
                "model": "ridge",
                "feature_set": "core_independent_microstructure",
                "train_rows": int(len(train_sample)),
                "validation_rows": int(len(validation_sample)),
                **metrics,
            }
        ]
    )


def write_csv(output_dir: Path, filename: str, frame: pd.DataFrame) -> pd.DataFrame:
    frame.to_csv(
        output_dir / filename, index=False, float_format="%.12g", na_rep="", lineterminator="\n"
    )
    return frame


def write_figures(
    output_dir: Path,
    regression: pd.DataFrame,
    daily: pd.DataFrame,
    ablation: pd.DataFrame,
    pred_corr: pd.DataFrame,
    feature_importance: pd.DataFrame,
    classification: pd.DataFrame,
    calibration_frame: pd.DataFrame,
    predictions: list[pd.DataFrame],
) -> None:
    figures = output_dir / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    _bar(
        regression,
        "model",
        "mean_daily_spearman_ic",
        figures / "model_mean_daily_ic_comparison.png",
        "Mean Daily IC",
    )
    _bar(
        ablation,
        "model",
        "mean_delta_daily_ic",
        figures / "delta_ic_vs_qi_baseline.png",
        "Delta IC vs QI",
    )
    _per_date(daily, figures / "per_date_qi_ridge_lightgbm_ic.png")
    _bar(
        pred_corr[pred_corr["reference_feature"] == "qi_1"],
        "prediction_column",
        "spearman_rank_correlation",
        figures / "prediction_vs_qi_rank_correlation.png",
        "Prediction vs QI Rank Correlation",
    )
    _importance(feature_importance, figures / "lightgbm_feature_importance.png")
    for feature in ["qi_1", "ofi_1s", "trade_imbalance_1s"]:
        _shape(predictions, feature, figures / f"prediction_shape_vs_{feature}.png")
    _bar(
        classification,
        "model",
        "roc_auc",
        figures / "next_mid_classification_auc_comparison.png",
        "Classification AUC",
    )
    _calibration(calibration_frame, figures / "next_mid_probability_calibration.png")


def _bar(frame: pd.DataFrame, x_col: str, y_col: str, path: Path, title: str) -> None:
    plot = frame.dropna(subset=[y_col]).copy()
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(np.arange(len(plot)), plot[y_col])
    ax.set_xticks(np.arange(len(plot)), plot[x_col].astype(str), rotation=45, ha="right")
    ax.set_title(title)
    ax.axhline(0, color="black", linewidth=0.8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _per_date(daily: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(9, 4))
    choices = [
        ("qi_linear", "qi_only"),
        ("ridge", "extended_book_flow"),
        ("lightgbm_regression", "extended_book_flow"),
    ]
    for model, feature_set in choices:
        subset = daily[(daily["model"] == model) & (daily["feature_set"] == feature_set)]
        if subset["spearman_ic"].notna().any():
            x_positions = np.arange(len(subset))
            ax.plot(
                x_positions, subset["spearman_ic"], marker="o", label=f"{model}/{feature_set}"
            )
            ax.set_xticks(x_positions, subset["date"], rotation=45, ha="right")
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_title("Per-Date IC")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _importance(frame: pd.DataFrame, path: Path) -> None:
    subset = frame[frame["importance_type"] == "lightgbm_gain"].copy()
    fig, ax = plt.subplots(figsize=(7, 4))
    if subset.empty:
        ax.text(0.5, 0.5, "LightGBM unavailable", ha="center", va="center")
        ax.set_axis_off()
    else:
        subset = subset.sort_values("importance", ascending=False).head(15)
        ax.bar(np.arange(len(subset)), subset["importance"])
        ax.set_xticks(np.arange(len(subset)), subset["feature"], rotation=45, ha="right")
    ax.set_title("LightGBM Feature Importance")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _shape(predictions: list[pd.DataFrame], feature: str, path: Path) -> None:
    frame = pd.concat(predictions, ignore_index=True)
    pred_cols = [column for column in frame.columns if "ridge_extended_book_flow" in column]
    pred_col = (
        pred_cols[0]
        if pred_cols
        else [column for column in frame.columns if column.startswith("pred_")][0]
    )
    valid = frame[[feature, pred_col]].dropna().sort_values(feature, kind="mergesort")
    if valid.empty:
        rows = pd.DataFrame({"bin": [], "prediction": []})
    else:
        valid["bin"] = (np.floor(np.arange(len(valid)) * 20 / len(valid)).astype(int) + 1).clip(
            1, 20
        )
        rows = valid.groupby("bin", sort=True)[pred_col].mean().reset_index(name="prediction")
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(rows["bin"], rows["prediction"], marker="o")
    ax.set_title(f"Prediction Shape vs {feature}")
    ax.set_xlabel("Feature bin")
    ax.set_ylabel("Mean prediction")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _calibration(frame: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(6, 4))
    for model, group in frame.groupby("model", sort=True):
        ax.plot(group["decile"], group["observed_up_rate"], marker="o", label=model)
    ax.set_title("Next-Mid Probability Calibration")
    ax.set_xlabel("Prediction decile")
    ax.set_ylabel("Observed up rate")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    if args.clean and output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    validate_dates(list(TRAIN_DATES), list(VALIDATION_DATES))
    plan = load_yaml_config(args.plan)
    plan_hash = hash_config(plan)
    snapshot = load_snapshot_manifest(args.snapshot)
    if snapshot["snapshot_hash"] != plan["frozen_inputs"]["pre_phase7_snapshot_hash"]:
        raise ValueError("Phase 8 snapshot hash does not match frozen plan")
    phase7 = json.loads(Path(args.phase7_summary).read_text(encoding="utf-8"))
    audit = json.loads(Path(args.phase7_audit_summary).read_text(encoding="utf-8"))
    if phase7["phase7_results_hash"] != PHASE7_RESULTS_HASH:
        raise ValueError("Phase 7 result hash mismatch")
    if audit["audit_result_hash"] != PHASE7_AUDIT_HASH:
        raise ValueError("Phase 7 audit hash mismatch")
    if plan["frozen_inputs"]["phase7_audit_commit"] != PHASE7_AUDIT_COMMIT:
        raise ValueError("Phase 7 audit commit mismatch")

    root = Path(args.derived_root)
    train = read_model_frame(root, TRAIN_DATES, "train")
    validation = read_model_frame(root, VALIDATION_DATES, "validation")
    lgb_module, lgb_error = try_lightgbm()

    regression_rows = []
    daily_rows = []
    prediction_frames = []
    importance_rows = []
    for model, feature_set in regression_plan():
        print(f"phase8 regression {model}/{feature_set}", flush=True)
        result, daily, predictions, importance = fit_predict_regression(
            model_name=model,
            feature_set=feature_set,
            train=train,
            validation=validation,
            lgb_module=lgb_module,
            lgb_error=lgb_error,
        )
        regression_rows.append(result)
        daily_rows.append(daily)
        prediction_frames.append(predictions)
        importance_rows.extend(importance)

    regression = add_daily_summaries(
        pd.DataFrame(regression_rows), pd.concat(daily_rows, ignore_index=True)
    )
    daily = pd.concat(daily_rows, ignore_index=True)
    ablation = ablations(regression, daily)
    pred_corr = prediction_correlations(prediction_frames)
    feature_importance = pd.DataFrame(importance_rows)
    if feature_importance.empty:
        feature_importance = pd.DataFrame(
            [
                {
                    "model": "lightgbm_regression",
                    "feature_set": "",
                    "feature": "",
                    "importance_type": "unavailable",
                    "importance": math.nan,
                }
            ]
        )
    classification, calibration_frame = classification_run(train, validation, lgb_module, lgb_error)
    negative = negative_control(train, validation)

    tables = {
        "regression_results.csv": regression,
        "daily_regression_metrics.csv": daily,
        "ablation_results.csv": ablation,
        "prediction_correlations.csv": pred_corr,
        "feature_importance.csv": feature_importance,
        "classification_results.csv": classification,
        "classification_calibration.csv": calibration_frame,
        "negative_control.csv": negative,
    }
    for filename, frame in tables.items():
        write_csv(output_dir, filename, frame)
    summary = build_summary(
        plan_hash, train, validation, regression, ablation, classification, negative, lgb_error
    )
    (output_dir / "README.md").write_text(readme_text(summary), encoding="utf-8")
    (output_dir / "phase8_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    result_hash = deterministic_results_hash(output_dir)
    summary["phase8_results_hash"] = result_hash
    (output_dir / "phase8_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    write_figures(
        output_dir,
        regression,
        daily,
        ablation,
        pred_corr,
        feature_importance,
        classification,
        calibration_frame,
        prediction_frames,
    )
    print(
        json.dumps(
            {"phase8_results_hash": result_hash, "phase8_status": summary["phase8_status"]},
            sort_keys=True,
        )
    )
    return 0 if summary["phase8_status"] in {"PASS", "PASS_WITH_LOCAL_LIGHTGBM_UNAVAILABLE"} else 1


def build_summary(
    plan_hash: str,
    train: pd.DataFrame,
    validation: pd.DataFrame,
    regression: pd.DataFrame,
    ablation: pd.DataFrame,
    classification: pd.DataFrame,
    negative: pd.DataFrame,
    lgb_error: str,
) -> dict[str, Any]:
    qi = regression[
        (regression["model"] == "qi_linear") & (regression["feature_set"] == "qi_only")
    ].iloc[0]
    best_ridge = (
        regression[regression["model"] == "ridge"]
        .sort_values("mean_daily_spearman_ic", ascending=False)
        .head(1)
    )
    lgb_available = not regression[
        (regression["model"] == "lightgbm_regression") & (regression["status"] == "fit")
    ].empty
    return {
        "phase": "Phase 8 - Baseline Predictive Modeling",
        "phase8_status": "PASS" if lgb_available else "PASS_WITH_LOCAL_LIGHTGBM_UNAVAILABLE",
        "phase8_modeling_plan_hash": plan_hash,
        "phase7_results_hash": PHASE7_RESULTS_HASH,
        "phase7_audit_hash": PHASE7_AUDIT_HASH,
        "phase7_audit_commit": PHASE7_AUDIT_COMMIT,
        "train_dates": list(TRAIN_DATES),
        "validation_dates": list(VALIDATION_DATES),
        "target": TARGET,
        "anchor_rule": "row_index % 10 == 0",
        "train_rows": int(len(train.dropna(subset=[TARGET]))),
        "validation_rows": int(len(validation.dropna(subset=[TARGET]))),
        "holdout_accessed": False,
        "qi_baseline_mean_daily_ic": _finite(qi["mean_daily_spearman_ic"]),
        "best_ridge": _records(best_ridge),
        "lightgbm_available_locally": lgb_available,
        "lightgbm_error": lgb_error,
        "classification": _records(classification),
        "negative_control": _records(negative),
        "top_incremental_lift": _records(
            ablation.sort_values("mean_delta_daily_ic", ascending=False).head(5)
        ),
        "limitations": [
            "2025 is development validation, not untouched holdout.",
            "2026 holdout was not accessed.",
            "Phase 8 is predictive modeling only and makes no trading or economic claim.",
            "Local LightGBM execution depends on a working local LightGBM stack.",
        ],
    }


def readme_text(summary: dict[str, Any]) -> str:
    return f"""# Phase 8 Baseline Predictive Modeling

Status: {summary["phase8_status"]}

Plan hash: `{summary["phase8_modeling_plan_hash"]}`

Train: 2024 development dates. Validation: 2025 development dates.

Primary target: `ret_fwd_1s`.

Primary sample: deterministic non-overlapping 1s anchors from the 100ms grid.

The central benchmark is QI-only. Complex models are evaluated by incremental daily IC
relative to QI, not by absolute score alone.

No 2026 holdout data, trading threshold, fill simulation, PnL, or backtest is used.
"""


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    clean = frame.replace({np.nan: None})
    return json.loads(clean.to_json(orient="records"))


def _finite(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


if __name__ == "__main__":
    raise SystemExit(main())
