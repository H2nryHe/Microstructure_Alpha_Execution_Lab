import numpy as np
import pandas as pd
import pytest

from microalpha.research.phase8 import (
    ANCHOR_OFFSET,
    ANCHOR_STRIDE,
    FEATURE_SETS,
    TRAIN_DATES,
    VALIDATION_DATES,
    TrainOnlyPreprocessor,
    anchor_mask,
    classification_metrics,
    deterministic_permutation,
    lift_summary,
    regression_metrics,
    validate_dates,
    validate_feature_columns,
)


def test_feature_sets_exclude_microprice_and_future_columns() -> None:
    for features in FEATURE_SETS.values():
        assert "microprice_deviation_bps" not in features
        validate_feature_columns(features)
    forbidden = [
        "ret_fwd_1s",
        "direction_1s",
        "future_mid_move_bps_1s",
        "future_move_in_spreads_1s",
        "next_mid_change_direction",
        "time_to_next_mid_change_ms",
        "target_time_1s",
        "actual_label_time_1s",
        "label_delay_ms_1s",
    ]
    for column in forbidden:
        with pytest.raises(ValueError, match="Future/target-derived"):
            validate_feature_columns(["qi_1", column])


def test_train_validation_dates_are_chronological_and_holdout_free() -> None:
    validate_dates(list(TRAIN_DATES), list(VALIDATION_DATES))
    with pytest.raises(ValueError, match="2026"):
        validate_dates(["2024-01-01"], ["2026-01-01"])
    with pytest.raises(ValueError, match="Training dates"):
        validate_dates(["2025-01-01"], ["2025-02-01"])
    with pytest.raises(ValueError, match="chronological"):
        validate_dates(["2024-02-01", "2024-01-01"], ["2025-01-01"])


def test_nonoverlapping_anchor_rule_is_deterministic_offset_zero() -> None:
    mask = anchor_mask(25)
    expected = [index in {0, 10, 20} for index in range(25)]
    assert mask.tolist() == expected
    assert ANCHOR_STRIDE == 10
    assert ANCHOR_OFFSET == 0


def test_train_only_preprocessor_does_not_read_validation_values() -> None:
    train = pd.DataFrame({"qi_1": [1.0, 2.0, np.nan], "trade_imbalance_1s": [np.nan, 1.0, 3.0]})
    validation = pd.DataFrame({"qi_1": [1000.0, np.nan], "trade_imbalance_1s": [1000.0, np.nan]})
    pre = TrainOnlyPreprocessor().fit(train, ["qi_1", "trade_imbalance_1s"])
    assert pre.medians["qi_1"] == 1.5
    assert pre.medians["trade_imbalance_1s"] == 2.0
    assert "trade_imbalance_1s" in pre.missing_indicator_features
    transformed_a = pre.transform(validation)
    validation_changed = validation.copy()
    validation_changed.loc[0, "qi_1"] = -9999.0
    transformed_b = pre.transform(validation_changed)
    assert transformed_a.shape == transformed_b.shape
    assert pre.medians["qi_1"] == 1.5


def test_regression_and_lift_metrics_are_defined() -> None:
    y = np.array([1.0, 2.0, -1.0, 0.0])
    pred = np.array([0.8, 2.1, -0.5, 0.1])
    metrics = regression_metrics(y, pred)
    assert metrics["row_count"] == 4
    assert metrics["spearman_ic"] > 0
    lift = lift_summary(pd.Series([0.2, 0.3]), pd.Series([0.1, 0.4]))
    assert lift["positive_lift_days"] == 1
    assert lift["negative_lift_days"] == 1


def test_classification_metrics_exclude_unavailable_outcomes_semantics() -> None:
    y = np.array([0, 1, 1, 0])
    prob = np.array([0.1, 0.8, 0.7, 0.2])
    metrics = classification_metrics(y, prob)
    assert metrics["roc_auc"] == pytest.approx(1.0)
    assert metrics["row_count"] == 4


def test_deterministic_permutation_is_stable() -> None:
    values = np.array([1.0, 2.0, np.nan, 3.0])
    first = deterministic_permutation(values)
    second = deterministic_permutation(values)
    np.testing.assert_equal(first, second)
    assert np.isnan(first[2])
