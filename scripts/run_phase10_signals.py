"""Run Phase 10 deterministic signal construction from frozen Phase 9 predictions."""

from __future__ import annotations

import argparse
import hashlib
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
from microalpha.pipeline.registry import DEVELOPMENT_DATES
from microalpha.research.phase7 import FEATURE_VERSION, LABEL_VERSION, load_snapshot_manifest
from microalpha.research.phase8 import SEED, TARGET, TrainOnlyPreprocessor, regression_metrics
from microalpha.research.phase9 import (
    LIGHTGBM_PARAMS,
    PHASE7_SNAPSHOT_HASH,
    PHASE8_MODELING_PLAN_HASH,
    PHASE8_RESULTS_HASH,
    PHASE9_PLAN_HASH,
    PRIMARY_MODELS,
    WALKFORWARD_FEATURE_SETS,
    WalkForwardFold,
    anchored_mask,
    build_expanding_folds,
)
from microalpha.research.phase10 import (
    PHASE7_AUDIT_HASH,
    PHASE7_RESULTS_HASH,
    PHASE9_ARTIFACT_COMMIT,
    PHASE9_RESULTS_HASH,
    PHASE10_PLAN_HASH,
    PHASE10_SIGNAL_ROOT,
    PRIMARY_RULE,
    SignalThresholds,
    compute_thresholds,
    deterministic_results_hash,
    generate_secondary_signals,
    generate_signals,
    run_lengths,
    signal_manifest_hash,
    summarize_run_lengths,
    transition_counts,
    verify_phase10_model_specs,
)
from microalpha.utils.hashing import hash_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--derived-root", default="/tmp/microalpha-multiday/derived")
    parser.add_argument("--snapshot", default="data/manifests/pre_phase7_research_snapshot.json")
    parser.add_argument("--plan", default="data/manifests/phase10_signal_plan.yaml")
    parser.add_argument("--phase9-summary", default="reports/phase9/phase9_summary.json")
    parser.add_argument("--phase9-metrics", default="reports/phase9/walkforward_metrics.csv")
    parser.add_argument("--output-dir", default="reports/phase10")
    parser.add_argument("--signal-root", default=str(PHASE10_SIGNAL_ROOT))
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


def read_model_frame(root: Path, date: str) -> pd.DataFrame:
    if date.startswith("2026-"):
        raise ValueError("Phase 10 must not load 2026 holdout dates")
    features = pd.read_parquet(feature_path(root, date), columns=required_feature_columns())
    labels = pd.read_parquet(label_path(root, date), columns=["observation_time", TARGET])
    if not features["observation_time"].reset_index(drop=True).equals(
        labels["observation_time"].reset_index(drop=True)
    ):
        raise ValueError(f"Feature/label timestamp mismatch for {date}")
    mask = anchored_mask(len(features))
    original_index = np.arange(len(features))[mask]
    frame = pd.concat(
        [
            features.loc[mask].reset_index(drop=True),
            labels.loc[mask].drop(columns=["observation_time"]).reset_index(drop=True),
        ],
        axis=1,
    )
    frame.insert(0, "date", date)
    frame.insert(1, "research_row_id", original_index.astype(int))
    for column in sorted(set(required_feature_columns()) - {"observation_time"} | {TARGET}):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("float64")
    return frame


def try_lightgbm() -> Any:
    try:
        import lightgbm as lgb
    except Exception as exc:  # noqa: BLE001 - dependency state is reported.
        message = f"LightGBM is required for Phase 10: {type(exc).__name__}: {exc}"
        raise RuntimeError(message) from exc
    return lgb


def verify_inputs(args: argparse.Namespace) -> dict[str, Any]:
    snapshot = load_snapshot_manifest(args.snapshot)
    if snapshot.get("snapshot_hash") != PHASE7_SNAPSHOT_HASH:
        raise ValueError("Unexpected Phase 7 snapshot hash")
    if any(str(date).startswith("2026-") for date in snapshot.get("included_dates", [])):
        raise ValueError("Snapshot includes 2026 holdout dates")
    plan_hash = hash_config(load_yaml_config(args.plan))
    if plan_hash != PHASE10_PLAN_HASH:
        raise ValueError(f"Phase 10 plan hash mismatch: {plan_hash}")
    phase9_summary = json.loads(Path(args.phase9_summary).read_text(encoding="utf-8"))
    if phase9_summary.get("phase9_plan_hash") != PHASE9_PLAN_HASH:
        raise ValueError("Unexpected Phase 9 plan hash")
    if phase9_summary.get("phase9_results_hash") != PHASE9_RESULTS_HASH:
        raise ValueError("Unexpected Phase 9 results hash")
    verify_phase10_model_specs()
    return {"phase10_signal_plan_hash": plan_hash}


def concat_dates(frames: dict[str, pd.DataFrame], dates: tuple[str, ...]) -> pd.DataFrame:
    return pd.concat([frames[date] for date in dates], ignore_index=True)


def fit_model_predictions(
    *,
    model: str,
    feature_set: str,
    train: pd.DataFrame,
    validation: pd.DataFrame,
    lgb_module: Any,
) -> tuple[np.ndarray, np.ndarray, int]:
    features = WALKFORWARD_FEATURE_SETS[feature_set]
    train_valid = train.dropna(subset=[TARGET]).copy()
    validation_valid = validation.dropna(subset=[TARGET]).copy()
    if model == "qi_direct_baseline":
        pre = TrainOnlyPreprocessor().fit(train_valid, features)
        fitted = LinearRegression().fit(pre.transform(train_valid), train_valid[TARGET])
        train_prediction = fitted.predict(pre.transform(train_valid))
        validation_prediction = fitted.predict(pre.transform(validation_valid))
    else:
        fitted = lgb_module.LGBMRegressor(**LIGHTGBM_PARAMS).fit(
            train_valid[features], train_valid[TARGET]
        )
        train_prediction = fitted.predict(train_valid[features])
        validation_prediction = fitted.predict(validation_valid[features])
    return (
        np.asarray(train_prediction, dtype=float),
        np.asarray(validation_prediction, dtype=float),
        len(train_valid),
    )


def model_config_hash(model: str, feature_set: str) -> str:
    payload = {
        "model": model,
        "feature_set": feature_set,
        "features": WALKFORWARD_FEATURE_SETS[feature_set],
        "lightgbm_params": LIGHTGBM_PARAMS if model.startswith("lightgbm") else {},
        "seed": SEED,
    }
    return hash_config(payload)


def validation_signal_frame(
    *,
    fold: WalkForwardFold,
    validation: pd.DataFrame,
    model: str,
    feature_set: str,
    prediction: np.ndarray,
    thresholds: SignalThresholds,
) -> pd.DataFrame:
    validation_valid = validation.dropna(subset=[TARGET]).copy()
    signal_input = pd.DataFrame(
        {
            "date": validation_valid["date"].to_numpy(),
            "signal_timestamp": validation_valid["observation_time"].to_numpy(),
            "observation_time": validation_valid["observation_time"].to_numpy(),
            "research_row_id": validation_valid["research_row_id"].to_numpy(dtype=int),
            "fold_id": fold.fold_id,
            "training_start_date": fold.train_dates[0],
            "training_end_date": fold.train_dates[-1],
            "validation_date": fold.validation_date,
            "model": model,
            "feature_set": feature_set,
            "model_config_hash": model_config_hash(model, feature_set),
            "phase9_results_hash": PHASE9_RESULTS_HASH,
            "phase10_signal_plan_hash": PHASE10_PLAN_HASH,
            "source_research_snapshot_hash": PHASE7_SNAPSHOT_HASH,
            "prediction": prediction,
            "valid_observation": True,
            "stale_observation": False,
        }
    )
    signals = generate_signals(signal_input, prediction_col="prediction", thresholds=thresholds)
    secondary = generate_secondary_signals(prediction, thresholds)
    for column, values in secondary.items():
        values = values.copy()
        if len(values):
            values[0] = 0
            values[-1] = 0
        signals[column] = values
    signals["q05_threshold"] = thresholds.q05
    signals["q10_threshold"] = thresholds.q10
    signals["q90_threshold"] = thresholds.q90
    signals["q95_threshold"] = thresholds.q95
    return signals


def write_signal_artifact(signal_root: Path, frame: pd.DataFrame) -> dict[str, Any]:
    date = str(frame["validation_date"].iloc[0])
    model = str(frame["model"].iloc[0])
    relative_id = f"date={date}/model={model}/signals.parquet"
    output_path = signal_root / relative_id
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(output_path, index=False)
    digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
    return {
        "relative_artifact_id": relative_id,
        "sha256": digest,
        "row_count": int(len(frame)),
        "schema": {column: str(dtype) for column, dtype in frame.dtypes.items()},
        "date": date,
        "model": model,
        "feature_set": str(frame["feature_set"].iloc[0]),
        "fold_id": int(frame["fold_id"].iloc[0]),
        "training_start_date": str(frame["training_start_date"].iloc[0]),
        "training_end_date": str(frame["training_end_date"].iloc[0]),
        "validation_date": date,
        "q05_threshold": float(frame["q05_threshold"].iloc[0]),
        "q10_threshold": float(frame["q10_threshold"].iloc[0]),
        "q90_threshold": float(frame["q90_threshold"].iloc[0]),
        "q95_threshold": float(frame["q95_threshold"].iloc[0]),
        "phase9_results_hash": PHASE9_RESULTS_HASH,
        "phase10_signal_plan_hash": PHASE10_PLAN_HASH,
    }


def reconcile_prediction_metrics(
    *,
    fold: WalkForwardFold,
    model: str,
    feature_set: str,
    validation: pd.DataFrame,
    prediction: np.ndarray,
    phase9_metrics: pd.DataFrame,
) -> dict[str, Any]:
    validation_valid = validation.dropna(subset=[TARGET])
    metrics = regression_metrics(validation_valid[TARGET].to_numpy(dtype=float), prediction)
    reference = phase9_metrics[
        (phase9_metrics["window"] == "expanding")
        & (phase9_metrics["fold_id"] == fold.fold_id)
        & (phase9_metrics["model"] == model)
    ].iloc[0]
    row = {
        "fold_id": fold.fold_id,
        "validation_date": fold.validation_date,
        "model": model,
        "feature_set": feature_set,
        "spearman_ic_regenerated": metrics["spearman_ic"],
        "spearman_ic_phase9": float(reference["spearman_ic"]),
        "spearman_abs_diff": abs(metrics["spearman_ic"] - float(reference["spearman_ic"])),
        "row_count_regenerated": int(metrics["row_count"]),
        "row_count_phase9": int(reference["row_count"]),
        "prediction_mean_regenerated": metrics["prediction_mean"],
        "prediction_mean_phase9": float(reference["prediction_mean"]),
        "prediction_std_regenerated": metrics["prediction_std"],
        "prediction_std_phase9": float(reference["prediction_std"]),
    }
    row["row_count_match"] = row["row_count_regenerated"] == row["row_count_phase9"]
    row["prediction_mean_abs_diff"] = abs(
        row["prediction_mean_regenerated"] - row["prediction_mean_phase9"]
    )
    row["prediction_std_abs_diff"] = abs(
        row["prediction_std_regenerated"] - row["prediction_std_phase9"]
    )
    row["status"] = (
        "PASS"
        if (
            row["spearman_abs_diff"] <= 1e-12
            and row["prediction_mean_abs_diff"] <= 1e-12
            and row["prediction_std_abs_diff"] <= 1e-12
            and row["row_count_match"]
        )
        else "FAIL"
    )
    return row


def signal_by_fold_rows(signals: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    rules = {
        PRIMARY_RULE: "final_signal",
        "train_q05_q95": "signal_train_q05_q95",
        "prediction_sign": "signal_prediction_sign",
    }
    for rule, column in rules.items():
        values = signals[column].astype(int)
        total = int(len(values))
        long_count = int((values == 1).sum())
        short_count = int((values == -1).sum())
        flat_count = int((values == 0).sum())
        rows.append(
            {
                "signal_rule": rule,
                "date": str(signals["validation_date"].iloc[0]),
                "fold_id": int(signals["fold_id"].iloc[0]),
                "model": str(signals["model"].iloc[0]),
                "feature_set": str(signals["feature_set"].iloc[0]),
                "total_eligible_anchors": total,
                "long_count": long_count,
                "short_count": short_count,
                "flat_count": flat_count,
                "long_coverage_pct": long_count / total if total else math.nan,
                "short_coverage_pct": short_count / total if total else math.nan,
                "active_coverage_pct": (long_count + short_count) / total if total else math.nan,
                "long_short_balance": long_count / short_count if short_count else math.nan,
            }
        )
    return rows


def transition_row(signals: pd.DataFrame) -> dict[str, Any]:
    final_counts = transition_counts(signals["final_signal"])
    raw_array = signals["raw_signal"].astype(int).to_numpy()
    raw_changes = int(
        (raw_array[1:] != raw_array[:-1]).sum()
    )
    total = len(signals)
    direct_reversals = final_counts["long_to_short"] + final_counts["short_to_long"]
    return {
        "date": str(signals["validation_date"].iloc[0]),
        "fold_id": int(signals["fold_id"].iloc[0]),
        "model": str(signals["model"].iloc[0]),
        **final_counts,
        "raw_signal_changes": raw_changes,
        "signal_transition_rate": final_counts["final_signal_changes"] / total
        if total
        else math.nan,
        "direct_reversal_rate": direct_reversals / total if total else math.nan,
    }


def persistence_rows(signals: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for name, value in [("LONG", 1), ("SHORT", -1)]:
        summary = summarize_run_lengths(run_lengths(signals["final_signal"], value))
        rows.append(
            {
                "date": str(signals["validation_date"].iloc[0]),
                "fold_id": int(signals["fold_id"].iloc[0]),
                "model": str(signals["model"].iloc[0]),
                "signal_side": name,
                "average_run_length_anchors": summary["average"],
                "median_run_length_anchors": summary["median"],
                "p95_run_length_anchors": summary["p95"],
                "max_run_length_anchors": summary["max"],
                "average_run_length_seconds": summary["average"],
                "median_run_length_seconds": summary["median"],
                "p95_run_length_seconds": summary["p95"],
                "max_run_length_seconds": summary["max"],
            }
        )
    return rows


def future_diagnostics(signals: pd.DataFrame, validation: pd.DataFrame) -> list[dict[str, Any]]:
    joined = signals.merge(
        validation[["research_row_id", TARGET]],
        on="research_row_id",
        how="left",
        validate="one_to_one",
    )
    rows = []
    for signal_value, name in [(-1, "SHORT"), (0, "FLAT"), (1, "LONG")]:
        subset = joined[joined["final_signal"] == signal_value]
        ret = subset[TARGET].astype(float)
        rows.append(
            {
                "date": str(joined["validation_date"].iloc[0]),
                "fold_id": int(joined["fold_id"].iloc[0]),
                "model": str(joined["model"].iloc[0]),
                "signal": name,
                "count": int(len(subset)),
                "mean_future_mid_return": finite_mean(ret),
                "median_future_mid_return": finite_median(ret),
                "p_return_gt_0": finite_probability(ret > 0),
                "p_return_lt_0": finite_probability(ret < 0),
                "mean_signed_future_mid_return": finite_mean(subset["final_signal"] * ret),
                "median_signed_future_mid_return": finite_median(subset["final_signal"] * ret),
                "positive_signed_fraction": finite_probability((subset["final_signal"] * ret) > 0),
            }
        )
    long_ret = joined[joined["final_signal"] == 1][TARGET].astype(float)
    short_ret = joined[joined["final_signal"] == -1][TARGET].astype(float)
    rows.append(
        {
            "date": str(joined["validation_date"].iloc[0]),
            "fold_id": int(joined["fold_id"].iloc[0]),
            "model": str(joined["model"].iloc[0]),
            "signal": "LONG_MINUS_SHORT",
            "count": int(len(long_ret) + len(short_ret)),
            "mean_future_mid_return": finite_mean(long_ret) - finite_mean(short_ret),
            "median_future_mid_return": finite_median(long_ret) - finite_median(short_ret),
            "p_return_gt_0": math.nan,
            "p_return_lt_0": math.nan,
            "mean_signed_future_mid_return": math.nan,
            "median_signed_future_mid_return": math.nan,
            "positive_signed_fraction": math.nan,
        }
    )
    return rows


def disagreement_rows(
    *,
    date: str,
    fold_id: int,
    left_model: str,
    right_model: str,
    left: pd.DataFrame,
    right: pd.DataFrame,
    validation: pd.DataFrame,
) -> list[dict[str, Any]]:
    merged = left[["research_row_id", "final_signal"]].merge(
        right[["research_row_id", "final_signal"]],
        on="research_row_id",
        suffixes=("_left", "_right"),
        validate="one_to_one",
    )
    merged = merged.merge(validation[["research_row_id", TARGET]], on="research_row_id", how="left")
    total = len(merged)
    rows = []
    mappings = [
        (0, 1, "LEFT_FLAT_TO_RIGHT_LONG"),
        (0, -1, "LEFT_FLAT_TO_RIGHT_SHORT"),
        (1, 0, "LEFT_LONG_TO_RIGHT_FLAT"),
        (-1, 0, "LEFT_SHORT_TO_RIGHT_FLAT"),
        (1, -1, "LEFT_LONG_TO_RIGHT_SHORT"),
        (-1, 1, "LEFT_SHORT_TO_RIGHT_LONG"),
    ]
    for left_value, right_value, name in mappings:
        subset = merged[
            (merged["final_signal_left"] == left_value)
            & (merged["final_signal_right"] == right_value)
        ]
        rows.append(
            {
                "date": date,
                "fold_id": fold_id,
                "comparison": f"{left_model}_vs_{right_model}",
                "disagreement_type": name,
                "count": int(len(subset)),
                "fraction": len(subset) / total if total else math.nan,
                "mean_future_mid_return": finite_mean(subset[TARGET]),
                "median_future_mid_return": finite_median(subset[TARGET]),
                "p_return_gt_0": finite_probability(subset[TARGET] > 0),
                "p_return_lt_0": finite_probability(subset[TARGET] < 0),
            }
        )
    disagreement = merged["final_signal_left"] != merged["final_signal_right"]
    rows.append(
        {
            "date": date,
            "fold_id": fold_id,
            "comparison": f"{left_model}_vs_{right_model}",
            "disagreement_type": "ANY_DISAGREEMENT",
            "count": int(disagreement.sum()),
            "fraction": float(disagreement.mean()) if total else math.nan,
            "mean_future_mid_return": finite_mean(merged.loc[disagreement, TARGET]),
            "median_future_mid_return": finite_median(merged.loc[disagreement, TARGET]),
            "p_return_gt_0": finite_probability(merged.loc[disagreement, TARGET] > 0),
            "p_return_lt_0": finite_probability(merged.loc[disagreement, TARGET] < 0),
        }
    )
    return rows


def finite_mean(values: pd.Series) -> float:
    numeric = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    return float(numeric.mean()) if len(numeric) else math.nan


def finite_median(values: pd.Series) -> float:
    numeric = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    return float(numeric.median()) if len(numeric) else math.nan


def finite_probability(mask: pd.Series) -> float:
    if len(mask) == 0:
        return math.nan
    return float(pd.Series(mask).dropna().mean()) if len(pd.Series(mask).dropna()) else math.nan


def build_trace_sample(signals: list[pd.DataFrame]) -> pd.DataFrame:
    frame = pd.concat(signals, ignore_index=True)
    frame["distance_to_nearest_threshold"] = np.minimum(
        (frame["prediction"] - frame["short_threshold"]).abs(),
        (frame["prediction"] - frame["long_threshold"]).abs(),
    )
    samples = []
    for model in PRIMARY_MODELS:
        model_frame = frame[frame["model"] == model]
        for signal in [-1, 0, 1]:
            subset = model_frame[model_frame["final_signal"] == signal]
            if not subset.empty:
                samples.append(subset.head(4))
        samples.append(model_frame.sort_values("distance_to_nearest_threshold").head(4))
    sample = pd.concat(samples, ignore_index=True).drop_duplicates(
        subset=["date", "model", "research_row_id"]
    )
    columns = [
        "signal_timestamp",
        "model",
        "prediction",
        "short_threshold",
        "long_threshold",
        "raw_signal",
        "final_signal",
        "signal_reason",
        "date",
        "fold_id",
        "research_row_id",
    ]
    return sample[columns].head(60)


def summarize_results(
    *,
    signal_by_fold: pd.DataFrame,
    future: pd.DataFrame,
    transitions: pd.DataFrame,
    persistence: pd.DataFrame,
    disagreement: pd.DataFrame,
    thresholds: pd.DataFrame,
    reconciliation: pd.DataFrame,
    artifact_hash: str,
    input_info: dict[str, Any],
) -> dict[str, Any]:
    primary = signal_by_fold[signal_by_fold["signal_rule"] == PRIMARY_RULE]
    long_short = future[future["signal"] == "LONG_MINUS_SHORT"]
    signed = future[future["signal"].isin(["LONG", "SHORT"])]
    any_disagreement = disagreement[disagreement["disagreement_type"] == "ANY_DISAGREEMENT"]
    return sanitize_json(
        {
            "phase": "Phase 10 - Signal Construction",
            "phase10_status": "PASS" if (reconciliation["status"] == "PASS").all() else "FAIL",
            "phase9_artifact_commit": PHASE9_ARTIFACT_COMMIT,
            "phase9_ci": {
                "tests": {"run_id": 31353512353, "conclusion": "success"},
                "research_smoke": {"run_id": 31353512319, "conclusion": "success"},
            },
            "phase7_snapshot_hash": PHASE7_SNAPSHOT_HASH,
            "phase7_results_hash": PHASE7_RESULTS_HASH,
            "phase7_audit_hash": PHASE7_AUDIT_HASH,
            "phase8_modeling_plan_hash": PHASE8_MODELING_PLAN_HASH,
            "phase8_results_hash": PHASE8_RESULTS_HASH,
            "phase9_walkforward_plan_hash": PHASE9_PLAN_HASH,
            "phase9_results_hash": PHASE9_RESULTS_HASH,
            "phase10_signal_plan_hash": input_info["phase10_signal_plan_hash"],
            "phase10_signal_artifact_hash": artifact_hash,
            "phase10_results_hash": "",
            "holdout_accessed": False,
            "eligible_dates": [
                "2024-07-01",
                "2024-08-01",
                "2024-09-01",
                "2024-10-01",
                "2024-11-01",
                "2024-12-01",
                "2025-01-01",
                "2025-02-01",
                "2025-03-01",
                "2025-04-01",
                "2025-05-01",
                "2025-06-01",
                "2025-07-01",
                "2025-08-01",
                "2025-09-01",
                "2025-10-01",
                "2025-11-01",
                "2025-12-01",
            ],
            "primary_rule": {
                "name": PRIMARY_RULE,
                "short_boundary": "prediction <= training q10",
                "long_boundary": "prediction >= training q90",
            },
            "active_coverage_mean_by_model": primary.groupby("model")[
                "active_coverage_pct"
            ].mean().to_dict(),
            "long_short_separation_mean_by_model": long_short.groupby("model")[
                "mean_future_mid_return"
            ].mean().to_dict(),
            "signed_future_mid_effect_mean_by_model": signed.groupby("model")[
                "mean_signed_future_mid_return"
            ].mean().to_dict(),
            "transition_rate_mean_by_model": transitions.groupby("model")[
                "signal_transition_rate"
            ].mean().to_dict(),
            "disagreement_mean_fraction": any_disagreement.groupby("comparison")[
                "fraction"
            ].mean().to_dict(),
            "threshold_q10_mean_by_model": thresholds.groupby("model")["q10"].mean().to_dict(),
            "threshold_q90_mean_by_model": thresholds.groupby("model")["q90"].mean().to_dict(),
            "persistence_average_run_anchors": persistence.groupby(["model", "signal_side"])[
                "average_run_length_anchors"
            ].mean().to_dict(),
            "prediction_reconciliation_status": "PASS"
            if (reconciliation["status"] == "PASS").all()
            else "FAIL",
            "row_level_signal_artifact_count": int(len(thresholds)),
            "limitations": [
                "Phase 10 used only 2024-2025 development validation dates.",
                "2026 holdout was not accessed.",
                "Signals are desired directional states, not orders or fills.",
                "No transaction cost, latency, execution, or economic outcome model was applied.",
            ],
        }
    )


def sanitize_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): sanitize_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_json(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def write_outputs(
    *,
    output_dir: Path,
    signal_by_fold: pd.DataFrame,
    transitions: pd.DataFrame,
    persistence: pd.DataFrame,
    thresholds: pd.DataFrame,
    disagreement: pd.DataFrame,
    future: pd.DataFrame,
    signal_manifest: dict[str, Any],
    trace_sample: pd.DataFrame,
    reconciliation: pd.DataFrame,
    summary: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    signal_summary = signal_by_fold.groupby(["signal_rule", "model"], as_index=False).agg(
        active_coverage_pct=("active_coverage_pct", "mean"),
        long_coverage_pct=("long_coverage_pct", "mean"),
        short_coverage_pct=("short_coverage_pct", "mean"),
        total_eligible_anchors=("total_eligible_anchors", "sum"),
    )
    signal_summary.to_csv(output_dir / "signal_summary.csv", index=False, float_format="%.12g")
    signal_by_fold.to_csv(output_dir / "signal_by_fold.csv", index=False, float_format="%.12g")
    transitions.to_csv(output_dir / "signal_transitions.csv", index=False, float_format="%.12g")
    persistence.to_csv(output_dir / "signal_persistence.csv", index=False, float_format="%.12g")
    thresholds.to_csv(output_dir / "thresholds_by_fold.csv", index=False, float_format="%.12g")
    disagreement.to_csv(
        output_dir / "model_signal_disagreement.csv", index=False, float_format="%.12g"
    )
    future.to_csv(
        output_dir / "signal_future_mid_diagnostics.csv", index=False, float_format="%.12g"
    )
    trace_sample.to_csv(output_dir / "signal_trace_sample.csv", index=False, float_format="%.12g")
    reconciliation.to_csv(
        output_dir / "prediction_reconciliation.csv", index=False, float_format="%.12g"
    )
    (output_dir / "signal_manifest.json").write_text(
        json.dumps(signal_manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (output_dir / "README.md").write_text(readme_text(summary), encoding="utf-8")
    (output_dir / "phase10_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    results_hash = deterministic_results_hash(output_dir)
    summary["phase10_results_hash"] = results_hash
    (output_dir / "phase10_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    plot_outputs(
        output_dir,
        signal_by_fold,
        future,
        transitions,
        persistence,
        thresholds,
        disagreement,
    )


def readme_text(summary: dict[str, Any]) -> str:
    return f"""# Phase 10 Signal Construction

Status: {summary["phase10_status"]}

Plan hash: `{summary["phase10_signal_plan_hash"]}`

Signal artifact hash: `{summary["phase10_signal_artifact_hash"]}`

Phase 10 uses only regenerated Phase 9 expanding-window out-of-sample
predictions for 2024-2025 development validation dates. Row-level signal
artifacts are stored outside Git; this directory contains compact manifests,
summaries, diagnostics, and figures.
"""


def plot_outputs(
    output_dir: Path,
    signal_by_fold: pd.DataFrame,
    future: pd.DataFrame,
    transitions: pd.DataFrame,
    persistence: pd.DataFrame,
    thresholds: pd.DataFrame,
    disagreement: pd.DataFrame,
) -> None:
    figures = output_dir / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    primary = signal_by_fold[signal_by_fold["signal_rule"] == PRIMARY_RULE]
    line_plot(primary, "active_coverage_pct", figures / "active_signal_coverage_by_date_model.png")
    long_short = future[future["signal"] == "LONG_MINUS_SHORT"]
    line_plot(
        long_short.rename(columns={"mean_future_mid_return": "long_short_separation"}),
        "long_short_separation",
        figures / "long_short_future_mid_separation_by_date.png",
    )
    signed = future[future["signal"].isin(["LONG", "SHORT"])]
    line_plot(
        signed.groupby(["date", "model"], as_index=False)["mean_signed_future_mid_return"].mean(),
        "mean_signed_future_mid_return",
        figures / "mean_signed_future_mid_effect_by_date.png",
    )
    line_plot(transitions, "signal_transition_rate", figures / "signal_transition_rate_by_date.png")
    run_length_plot(persistence, figures / "long_short_run_length_distribution.png")
    threshold_plot(thresholds, figures / "threshold_drift_q10_q90.png")
    disagreement_plot(
        disagreement,
        "qi_direct_baseline_vs_lightgbm_extended",
        figures / "qi_vs_extended_signal_disagreement_rate.png",
    )
    disagreement_plot(
        disagreement,
        "lightgbm_qi_ofi_vs_lightgbm_extended",
        figures / "qi_ofi_vs_extended_signal_disagreement_rate.png",
    )
    conditional_plot(future, figures / "conditional_future_mid_return_by_signal.png")
    sparsity_plot(signal_by_fold, figures / "primary_vs_secondary_signal_sparsity.png")


def line_plot(frame: pd.DataFrame, y_col: str, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 4))
    dates = sorted(frame["date"].unique())
    x = np.arange(len(dates))
    for model in PRIMARY_MODELS:
        subset = frame[frame["model"] == model].set_index("date").reindex(dates)
        ax.plot(x, subset[y_col], marker="o", label=model)
    ax.set_xticks(x, dates, rotation=45, ha="right")
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_title(y_col)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def run_length_plot(frame: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 4))
    labels = []
    values = []
    for (model, side), group in frame.groupby(["model", "signal_side"], sort=True):
        labels.append(f"{model}\n{side}")
        values.append(group["average_run_length_anchors"].mean())
    ax.bar(np.arange(len(values)), values)
    ax.set_xticks(np.arange(len(values)), labels, rotation=45, ha="right")
    ax.set_title("Average Run Length")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def threshold_plot(frame: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 4))
    dates = sorted(frame["date"].unique())
    x = np.arange(len(dates))
    for model in PRIMARY_MODELS:
        subset = frame[frame["model"] == model].set_index("date").reindex(dates)
        ax.plot(x, subset["q10"], marker="o", label=f"{model} q10")
        ax.plot(x, subset["q90"], marker="x", label=f"{model} q90")
    ax.set_xticks(x, dates, rotation=45, ha="right")
    ax.set_title("q10/q90 Threshold Drift")
    ax.legend(fontsize=6, ncol=2)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def disagreement_plot(frame: pd.DataFrame, comparison: str, path: Path) -> None:
    subset = frame[
        (frame["comparison"] == comparison) & (frame["disagreement_type"] == "ANY_DISAGREEMENT")
    ]
    fig, ax = plt.subplots(figsize=(10, 4))
    x = np.arange(len(subset))
    ax.plot(x, subset["fraction"], marker="o")
    ax.set_xticks(x, subset["date"], rotation=45, ha="right")
    ax.set_title(comparison)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def conditional_plot(frame: pd.DataFrame, path: Path) -> None:
    subset = frame[frame["signal"].isin(["SHORT", "FLAT", "LONG"])]
    plot = subset.groupby(["model", "signal"], as_index=False)["mean_future_mid_return"].mean()
    fig, ax = plt.subplots(figsize=(8, 4))
    labels = []
    values = []
    for _, row in plot.iterrows():
        labels.append(f"{row['model']}\n{row['signal']}")
        values.append(row["mean_future_mid_return"])
    ax.bar(np.arange(len(values)), values)
    ax.set_xticks(np.arange(len(values)), labels, rotation=45, ha="right")
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_title("Conditional Future-Mid Return")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def sparsity_plot(frame: pd.DataFrame, path: Path) -> None:
    plot = frame.groupby(["signal_rule", "model"], as_index=False)["active_coverage_pct"].mean()
    fig, ax = plt.subplots(figsize=(8, 4))
    labels = [f"{row.signal_rule}\n{row.model}" for row in plot.itertuples()]
    ax.bar(np.arange(len(plot)), plot["active_coverage_pct"])
    ax.set_xticks(np.arange(len(plot)), labels, rotation=45, ha="right")
    ax.set_title("Signal Sparsity")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    signal_root = Path(args.signal_root)
    if args.clean:
        if output_dir.exists():
            shutil.rmtree(output_dir)
        if signal_root.exists():
            shutil.rmtree(signal_root)
    input_info = verify_inputs(args)
    lgb_module = try_lightgbm()
    all_dates = DEVELOPMENT_DATES
    frames = {date: read_model_frame(Path(args.derived_root), date) for date in all_dates}
    folds = build_expanding_folds(all_dates)
    phase9_metrics = pd.read_csv(args.phase9_metrics)
    signal_frames: list[pd.DataFrame] = []
    manifest_entries: list[dict[str, Any]] = []
    by_fold_rows: list[dict[str, Any]] = []
    transition_rows: list[dict[str, Any]] = []
    persistence_output: list[dict[str, Any]] = []
    threshold_rows: list[dict[str, Any]] = []
    future_rows: list[dict[str, Any]] = []
    reconciliation_rows: list[dict[str, Any]] = []
    signals_by_key: dict[tuple[str, str], pd.DataFrame] = {}
    for fold in folds:
        print(f"phase10 fold {fold.fold_id} validate {fold.validation_date}")
        train = concat_dates(frames, fold.train_dates)
        validation = frames[fold.validation_date]
        for model, feature_set in PRIMARY_MODELS.items():
            train_prediction, validation_prediction, train_row_count = fit_model_predictions(
                model=model,
                feature_set=feature_set,
                train=train,
                validation=validation,
                lgb_module=lgb_module,
            )
            thresholds = compute_thresholds(train_prediction)
            signals = validation_signal_frame(
                fold=fold,
                validation=validation,
                model=model,
                feature_set=feature_set,
                prediction=validation_prediction,
                thresholds=thresholds,
            )
            manifest_entries.append(write_signal_artifact(signal_root, signals))
            signal_frames.append(signals)
            signals_by_key[(fold.validation_date, model)] = signals
            by_fold_rows.extend(signal_by_fold_rows(signals))
            transition_rows.append(transition_row(signals))
            persistence_output.extend(persistence_rows(signals))
            future_rows.extend(future_diagnostics(signals, validation))
            reconciliation_rows.append(
                reconcile_prediction_metrics(
                    fold=fold,
                    model=model,
                    feature_set=feature_set,
                    validation=validation,
                    prediction=validation_prediction,
                    phase9_metrics=phase9_metrics,
                )
            )
            threshold_rows.append(
                {
                    "date": fold.validation_date,
                    "fold_id": fold.fold_id,
                    "model": model,
                    "feature_set": feature_set,
                    "train_row_count": train_row_count,
                    "q05": thresholds.q05,
                    "q10": thresholds.q10,
                    "q90": thresholds.q90,
                    "q95": thresholds.q95,
                }
            )
    disagreement_output: list[dict[str, Any]] = []
    for fold in folds:
        validation = frames[fold.validation_date]
        disagreement_output.extend(
            disagreement_rows(
                date=fold.validation_date,
                fold_id=fold.fold_id,
                left_model="qi_direct_baseline",
                right_model="lightgbm_extended",
                left=signals_by_key[(fold.validation_date, "qi_direct_baseline")],
                right=signals_by_key[(fold.validation_date, "lightgbm_extended")],
                validation=validation,
            )
        )
        disagreement_output.extend(
            disagreement_rows(
                date=fold.validation_date,
                fold_id=fold.fold_id,
                left_model="lightgbm_qi_ofi",
                right_model="lightgbm_extended",
                left=signals_by_key[(fold.validation_date, "lightgbm_qi_ofi")],
                right=signals_by_key[(fold.validation_date, "lightgbm_extended")],
                validation=validation,
            )
        )
    manifest_payload = {
        "artifact_identity": "phase10_signal_artifacts_v1",
        "phase10_signal_plan_hash": PHASE10_PLAN_HASH,
        "phase9_results_hash": PHASE9_RESULTS_HASH,
        "source_research_snapshot_hash": PHASE7_SNAPSHOT_HASH,
        "entries": sorted(manifest_entries, key=lambda row: row["relative_artifact_id"]),
    }
    artifact_hash = signal_manifest_hash(manifest_payload)
    signal_manifest = {"phase10_signal_artifact_hash": artifact_hash, **manifest_payload}
    signal_by_fold = pd.DataFrame(by_fold_rows)
    transitions = pd.DataFrame(transition_rows)
    persistence = pd.DataFrame(persistence_output)
    thresholds = pd.DataFrame(threshold_rows)
    disagreement = pd.DataFrame(disagreement_output)
    future = pd.DataFrame(future_rows)
    trace_sample = build_trace_sample(signal_frames)
    reconciliation = pd.DataFrame(reconciliation_rows)
    summary = summarize_results(
        signal_by_fold=signal_by_fold,
        future=future,
        transitions=transitions,
        persistence=persistence,
        disagreement=disagreement,
        thresholds=thresholds,
        reconciliation=reconciliation,
        artifact_hash=artifact_hash,
        input_info=input_info,
    )
    write_outputs(
        output_dir=output_dir,
        signal_by_fold=signal_by_fold,
        transitions=transitions,
        persistence=persistence,
        thresholds=thresholds,
        disagreement=disagreement,
        future=future,
        signal_manifest=signal_manifest,
        trace_sample=trace_sample,
        reconciliation=reconciliation,
        summary=summary,
    )
    final_summary = json.loads((output_dir / "phase10_summary.json").read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "phase10_results_hash": final_summary["phase10_results_hash"],
                "phase10_signal_artifact_hash": final_summary["phase10_signal_artifact_hash"],
                "phase10_status": final_summary["phase10_status"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
