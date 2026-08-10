"""Phase 10 deterministic signal-construction helpers."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from microalpha.research.phase9 import (
    PRIMARY_MODELS,
    WALKFORWARD_FEATURE_SETS,
    WalkForwardFold,
    build_expanding_folds,
    validate_lightgbm_params,
    validate_model_columns,
    validate_walkforward_feature_sets,
)
from microalpha.utils.hashing import hash_config

PHASE7_RESULTS_HASH = "b86d51c4317f87d0cabf579f152d07c139e7fc23e47356d655bd09057342eb04"
PHASE7_AUDIT_HASH = "b6b8206e03c81b47787d5ae4d4e5b960b4748bc75eed0ee5be4862ebf190d6e1"
PHASE9_RESULTS_HASH = "0e6567e9f67954df4ec5c74233f4e1d34759e4f6b7bf1f562be2e63997a44aee"
PHASE9_ARTIFACT_COMMIT = "840465559903abf25857bf24a899202c2bbc9f47"
PHASE10_PLAN_HASH = "0ae8590cef7e7ea313c80889c74cc7db592a948f119e3982a2f2269df0c2a2bb"
PHASE10_SIGNAL_ROOT = Path("/tmp/microalpha-phase10/signals")
PRIMARY_RULE = "train_q10_q90"
SECONDARY_RULES = ("train_q05_q95", "prediction_sign")
SIGNAL_VALUES = {-1, 0, 1}

FORBIDDEN_SIGNAL_COLUMNS = {
    "next_mid_change_direction",
    "time_to_next_mid_change_ms",
}
FORBIDDEN_SIGNAL_PREFIXES = (
    "ret_fwd_",
    "future_mid_move_",
    "future_move_in_spreads_",
    "direction_",
    "target_time_",
    "actual_label_time_",
    "label_delay_ms_",
)


@dataclass(frozen=True)
class SignalThresholds:
    q05: float
    q10: float
    q90: float
    q95: float

    @property
    def primary_short(self) -> float:
        return self.q10

    @property
    def primary_long(self) -> float:
        return self.q90


def verify_phase10_model_specs() -> None:
    validate_walkforward_feature_sets()
    validate_lightgbm_params()
    if tuple(PRIMARY_MODELS) != (
        "qi_direct_baseline",
        "lightgbm_qi_ofi",
        "lightgbm_extended",
    ):
        raise ValueError("Phase 10 must use exact Phase 9 model candidates")
    for feature_set in WALKFORWARD_FEATURE_SETS.values():
        validate_model_columns(feature_set)


def compute_thresholds(train_predictions: np.ndarray) -> SignalThresholds:
    values = np.asarray(train_predictions, dtype=float)
    finite = values[np.isfinite(values)]
    if len(finite) == 0:
        raise ValueError("Cannot estimate thresholds from all-nonfinite training predictions")
    quantiles = np.quantile(finite, [0.05, 0.10, 0.90, 0.95], method="linear")
    return SignalThresholds(*(float(value) for value in quantiles))


def validate_signal_generator_inputs(frame: pd.DataFrame) -> None:
    for column in frame.columns:
        if column in FORBIDDEN_SIGNAL_COLUMNS or column.startswith(FORBIDDEN_SIGNAL_PREFIXES):
            raise ValueError(f"Future-derived column cannot enter signal generator: {column}")


def raw_signal_reason(
    prediction: float, short_threshold: float, long_threshold: float
) -> tuple[int, str]:
    if not math.isfinite(float(prediction)):
        return 0, "NONFINITE_PREDICTION"
    if prediction >= long_threshold:
        return 1, "LONG_THRESHOLD"
    if prediction <= short_threshold:
        return -1, "SHORT_THRESHOLD"
    return 0, "INSIDE_BAND"


def apply_risk_rules(
    *,
    raw_signal: int,
    reason: str,
    valid_observation: bool,
    stale_observation: bool,
    is_day_start: bool,
    is_day_end: bool,
) -> tuple[int, str]:
    if raw_signal not in SIGNAL_VALUES:
        raise ValueError(f"Invalid raw signal: {raw_signal}")
    if not valid_observation or stale_observation:
        return 0, "INVALID_OBSERVATION"
    if is_day_start or is_day_end:
        return 0, "DAY_BOUNDARY_FLAT"
    return raw_signal, reason


def generate_signals(
    frame: pd.DataFrame,
    *,
    prediction_col: str,
    thresholds: SignalThresholds,
    rule_name: str = PRIMARY_RULE,
) -> pd.DataFrame:
    validate_signal_generator_inputs(frame)
    if prediction_col not in frame.columns:
        raise ValueError(f"Missing prediction column: {prediction_col}")
    if "valid_observation" not in frame.columns:
        raise ValueError("Signal generator requires valid_observation")
    if "stale_observation" not in frame.columns:
        raise ValueError("Signal generator requires stale_observation")
    output = frame.copy()
    raw_values: list[int] = []
    raw_reasons: list[str] = []
    final_values: list[int] = []
    final_reasons: list[str] = []
    for idx, row in output.iterrows():
        raw, reason = raw_signal_reason(
            float(row[prediction_col]), thresholds.primary_short, thresholds.primary_long
        )
        final, final_reason = apply_risk_rules(
            raw_signal=raw,
            reason=reason,
            valid_observation=bool(row["valid_observation"]),
            stale_observation=bool(row["stale_observation"]),
            is_day_start=idx == output.index[0],
            is_day_end=idx == output.index[-1],
        )
        raw_values.append(raw)
        raw_reasons.append(reason)
        final_values.append(final)
        final_reasons.append(final_reason)
    output["signal_rule"] = rule_name
    output["short_threshold"] = thresholds.primary_short
    output["long_threshold"] = thresholds.primary_long
    output["raw_signal"] = raw_values
    output["raw_signal_reason"] = raw_reasons
    output["final_signal"] = final_values
    output["signal_reason"] = final_reasons
    if not set(output["final_signal"]).issubset(SIGNAL_VALUES):
        raise ValueError("Final signal outside {-1, 0, 1}")
    return output


def generate_secondary_signals(
    predictions: np.ndarray, thresholds: SignalThresholds
) -> dict[str, np.ndarray]:
    values = np.asarray(predictions, dtype=float)
    q05_q95 = np.zeros(len(values), dtype=int)
    q05_q95[np.isfinite(values) & (values <= thresholds.q05)] = -1
    q05_q95[np.isfinite(values) & (values >= thresholds.q95)] = 1
    sign = np.zeros(len(values), dtype=int)
    sign[np.isfinite(values) & (values < 0)] = -1
    sign[np.isfinite(values) & (values > 0)] = 1
    return {"signal_train_q05_q95": q05_q95, "signal_prediction_sign": sign}


def probability_signal(
    probabilities: np.ndarray,
    *,
    short_probability_threshold: float,
    long_probability_threshold: float,
) -> np.ndarray:
    if short_probability_threshold > long_probability_threshold:
        raise ValueError("Short probability threshold cannot exceed long threshold")
    values = np.asarray(probabilities, dtype=float)
    signal = np.zeros(len(values), dtype=int)
    signal[np.isfinite(values) & (values <= short_probability_threshold)] = -1
    signal[np.isfinite(values) & (values >= long_probability_threshold)] = 1
    return signal


def transition_counts(signals: pd.Series) -> dict[str, Any]:
    values = signals.astype(int).to_numpy()
    rows = {
        "raw_signal_changes": 0,
        "final_signal_changes": 0,
        "long_to_flat": 0,
        "flat_to_long": 0,
        "short_to_flat": 0,
        "flat_to_short": 0,
        "long_to_short": 0,
        "short_to_long": 0,
    }
    if len(values) < 2:
        return rows
    changes = values[1:] != values[:-1]
    rows["final_signal_changes"] = int(changes.sum())
    for previous, current in zip(values[:-1], values[1:], strict=True):
        if previous == 1 and current == 0:
            rows["long_to_flat"] += 1
        elif previous == 0 and current == 1:
            rows["flat_to_long"] += 1
        elif previous == -1 and current == 0:
            rows["short_to_flat"] += 1
        elif previous == 0 and current == -1:
            rows["flat_to_short"] += 1
        elif previous == 1 and current == -1:
            rows["long_to_short"] += 1
        elif previous == -1 and current == 1:
            rows["short_to_long"] += 1
    return rows


def run_lengths(signals: pd.Series, target_signal: int) -> list[int]:
    lengths: list[int] = []
    current = 0
    for value in signals.astype(int):
        if value == target_signal:
            current += 1
        elif current:
            lengths.append(current)
            current = 0
    if current:
        lengths.append(current)
    return lengths


def summarize_run_lengths(lengths: list[int]) -> dict[str, float]:
    if not lengths:
        return {"average": 0.0, "median": 0.0, "p95": 0.0, "max": 0.0}
    arr = np.asarray(lengths, dtype=float)
    return {
        "average": float(arr.mean()),
        "median": float(np.median(arr)),
        "p95": float(np.quantile(arr, 0.95)),
        "max": float(arr.max()),
    }


def signal_manifest_hash(manifest: dict[str, Any]) -> str:
    return hash_config(manifest)


def deterministic_results_hash(output_dir: str | Path) -> str:
    directory = Path(output_dir)
    files = [
        "signal_summary.csv",
        "signal_by_fold.csv",
        "signal_transitions.csv",
        "signal_persistence.csv",
        "thresholds_by_fold.csv",
        "model_signal_disagreement.csv",
        "signal_future_mid_diagnostics.csv",
        "signal_manifest.json",
        "signal_trace_sample.csv",
        "prediction_reconciliation.csv",
        "phase10_summary.json",
        "README.md",
    ]
    payload: dict[str, Any] = {}
    for file in files:
        content = (directory / file).read_text(encoding="utf-8")
        if file == "phase10_summary.json":
            summary = json.loads(content)
            summary.pop("phase10_results_hash", None)
            payload[file] = summary
        else:
            payload[file] = content
    return hash_config(payload)


def expected_expanding_folds() -> list[WalkForwardFold]:
    return build_expanding_folds()
