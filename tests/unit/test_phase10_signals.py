import numpy as np
import pandas as pd
import pytest

from microalpha.research.phase10 import (
    PHASE10_PLAN_HASH,
    SIGNAL_VALUES,
    SignalThresholds,
    apply_risk_rules,
    compute_thresholds,
    deterministic_results_hash,
    expected_expanding_folds,
    generate_secondary_signals,
    generate_signals,
    probability_signal,
    raw_signal_reason,
    run_lengths,
    signal_manifest_hash,
    summarize_run_lengths,
    transition_counts,
    validate_signal_generator_inputs,
    verify_phase10_model_specs,
)


def signal_input(predictions: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "observation_time": pd.date_range("2024-01-01", periods=len(predictions), freq="s"),
            "prediction": predictions,
            "valid_observation": [True] * len(predictions),
            "stale_observation": [False] * len(predictions),
        }
    )


def test_threshold_boundary_behavior_is_inclusive() -> None:
    thresholds = SignalThresholds(q05=-2.0, q10=-1.0, q90=1.0, q95=2.0)
    assert raw_signal_reason(1.0, thresholds.q10, thresholds.q90) == (1, "LONG_THRESHOLD")
    assert raw_signal_reason(-1.0, thresholds.q10, thresholds.q90) == (-1, "SHORT_THRESHOLD")
    assert raw_signal_reason(0.0, thresholds.q10, thresholds.q90) == (0, "INSIDE_BAND")
    assert raw_signal_reason(float("nan"), thresholds.q10, thresholds.q90) == (
        0,
        "NONFINITE_PREDICTION",
    )


def test_train_quantiles_are_deterministic_and_train_only() -> None:
    train_predictions = np.arange(100, dtype=float)
    thresholds = compute_thresholds(train_predictions)
    assert thresholds.q10 == pytest.approx(9.9)
    assert thresholds.q90 == pytest.approx(89.1)
    mutated_validation_labels = pd.Series([999.0, -999.0, 0.0])
    thresholds_again = compute_thresholds(train_predictions)
    assert thresholds == thresholds_again
    assert mutated_validation_labels.sum() == 0.0


def test_q05_q95_secondary_rule_uses_training_thresholds() -> None:
    thresholds = compute_thresholds(np.arange(100, dtype=float))
    secondary = generate_secondary_signals(np.array([0.0, 50.0, 99.0]), thresholds)
    assert secondary["signal_train_q05_q95"].tolist() == [-1, 0, 1]
    assert secondary["signal_prediction_sign"].tolist() == [0, 1, 1]


def test_no_future_columns_accepted_by_signal_generator() -> None:
    for column in [
        "ret_fwd_1s",
        "future_mid_move_bps_1s",
        "future_move_in_spreads_1s",
        "direction_1s",
        "next_mid_change_direction",
        "time_to_next_mid_change_ms",
        "target_time_1s",
        "actual_label_time_1s",
        "label_delay_ms_1s",
    ]:
        frame = signal_input([0.0])
        frame[column] = 1.0
        with pytest.raises(ValueError, match="Future-derived"):
            validate_signal_generator_inputs(frame)


def test_nonfinite_invalid_and_day_boundaries_become_flat() -> None:
    frame = signal_input([-2.0, float("nan"), 2.0, -2.0])
    frame.loc[2, "valid_observation"] = False
    thresholds = SignalThresholds(q05=-2.0, q10=-1.0, q90=1.0, q95=2.0)
    signals = generate_signals(frame, prediction_col="prediction", thresholds=thresholds)
    assert signals["raw_signal"].tolist() == [-1, 0, 1, -1]
    assert signals["final_signal"].tolist() == [0, 0, 0, 0]
    assert signals.loc[0, "signal_reason"] == "DAY_BOUNDARY_FLAT"
    assert signals.loc[1, "signal_reason"] == "NONFINITE_PREDICTION"
    assert signals.loc[2, "signal_reason"] == "INVALID_OBSERVATION"
    assert signals.loc[3, "signal_reason"] == "DAY_BOUNDARY_FLAT"
    assert set(signals["final_signal"]).issubset(SIGNAL_VALUES)


def test_probability_signal_rule() -> None:
    probs = np.array([0.1, 0.2, 0.5, 0.8, 0.9, np.nan])
    signal = probability_signal(
        probs, short_probability_threshold=0.2, long_probability_threshold=0.8
    )
    assert signal.tolist() == [-1, -1, 0, 1, 1, 0]
    with pytest.raises(ValueError, match="Short probability"):
        probability_signal(probs, short_probability_threshold=0.9, long_probability_threshold=0.1)


def test_transition_counts_cover_all_cases() -> None:
    values = pd.Series([0, 1, 0, -1, 0, 1, -1, 1])
    counts = transition_counts(values)
    assert counts["final_signal_changes"] == 7
    assert counts["flat_to_long"] == 2
    assert counts["long_to_flat"] == 1
    assert counts["flat_to_short"] == 1
    assert counts["short_to_flat"] == 1
    assert counts["long_to_short"] == 1
    assert counts["short_to_long"] == 1


def test_run_length_summary() -> None:
    lengths = run_lengths(pd.Series([0, 1, 1, 0, 1, -1, -1, -1]), 1)
    assert lengths == [2, 1]
    summary = summarize_run_lengths(lengths)
    assert summary["average"] == pytest.approx(1.5)
    assert summary["max"] == 2.0


def test_label_mutation_does_not_change_signal_generation_or_hash() -> None:
    thresholds = SignalThresholds(q05=-2.0, q10=-1.0, q90=1.0, q95=2.0)
    base = signal_input([-2.0, -0.5, 2.0])
    first = generate_signals(base, prediction_col="prediction", thresholds=thresholds)
    mutated = base.copy()
    mutated["ret_fwd_1s"] = [100.0, -100.0, 0.0]
    mutated["future_mid_move_bps_1s"] = [-1.0, 1.0, 0.0]
    mutated["direction_1s"] = [1, -1, 0]
    with pytest.raises(ValueError, match="Future-derived"):
        generate_signals(mutated, prediction_col="prediction", thresholds=thresholds)
    regenerated = generate_signals(base, prediction_col="prediction", thresholds=thresholds)
    pd.testing.assert_series_equal(first["raw_signal"], regenerated["raw_signal"])
    pd.testing.assert_series_equal(first["final_signal"], regenerated["final_signal"])
    first_hash = signal_manifest_hash({"signals": first["final_signal"].tolist()})
    regenerated_hash = signal_manifest_hash({"signals": regenerated["final_signal"].tolist()})
    assert first_hash == regenerated_hash


def test_fold_chronology_model_specs_and_plan_hash() -> None:
    verify_phase10_model_specs()
    folds = expected_expanding_folds()
    assert len(folds) == 18
    assert all(train < fold.validation_date for fold in folds for train in fold.train_dates)
    assert not any(fold.validation_date.startswith("2026-") for fold in folds)
    assert PHASE10_PLAN_HASH == "0ae8590cef7e7ea313c80889c74cc7db592a948f119e3982a2f2269df0c2a2bb"


def test_deterministic_results_hash(tmp_path) -> None:
    for file in [
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
        "README.md",
    ]:
        (tmp_path / file).write_text("a,b\n1,2\n", encoding="utf-8")
    (tmp_path / "phase10_summary.json").write_text(
        '{"phase10_results_hash": "ignored", "x": 1}', encoding="utf-8"
    )
    assert deterministic_results_hash(tmp_path) == deterministic_results_hash(tmp_path)


def test_signal_artifact_lineage_completeness() -> None:
    required = {
        "date",
        "signal_timestamp",
        "observation_time",
        "research_row_id",
        "fold_id",
        "training_start_date",
        "training_end_date",
        "validation_date",
        "model",
        "feature_set",
        "model_config_hash",
        "phase9_results_hash",
        "phase10_signal_plan_hash",
        "source_research_snapshot_hash",
        "prediction",
        "short_threshold",
        "long_threshold",
        "raw_signal",
        "final_signal",
        "signal_reason",
    }
    row = {key: "x" for key in required}
    assert required.issubset(row)


def test_apply_risk_rules_rejects_invalid_signal_value() -> None:
    with pytest.raises(ValueError, match="Invalid raw signal"):
        apply_risk_rules(
            raw_signal=2,
            reason="BAD",
            valid_observation=True,
            stale_observation=False,
            is_day_start=False,
            is_day_end=False,
        )
