import numpy as np
import pandas as pd
import pytest

from microalpha.pipeline.registry import DEVELOPMENT_DATES
from microalpha.research.phase8 import ANCHOR_OFFSET, ANCHOR_STRIDE
from microalpha.research.phase9 import (
    LIGHTGBM_PARAMS,
    PHASE9_PLAN_HASH,
    WALKFORWARD_FEATURE_SETS,
    WalkForwardFold,
    anchored_mask,
    build_expanding_folds,
    build_rolling6_folds,
    delta_summary,
    deterministic_results_hash,
    fold_delta_rows,
    sign_test,
    validate_development_dates,
    validate_folds,
    validate_lightgbm_params,
    validate_model_columns,
    validate_walkforward_feature_sets,
)


def test_expanding_folds_are_strict_and_complete() -> None:
    folds = build_expanding_folds()
    assert len(folds) == 18
    assert folds[0] == WalkForwardFold(
        fold_id=1,
        window="expanding",
        train_dates=DEVELOPMENT_DATES[:6],
        validation_date="2024-07-01",
    )
    assert folds[-1].validation_date == "2025-12-01"
    assert folds[-1].train_dates == DEVELOPMENT_DATES[:23]
    for fold in folds:
        assert all(train_date < fold.validation_date for train_date in fold.train_dates)


def test_rolling6_folds_are_strict_and_fixed_length() -> None:
    folds = build_rolling6_folds()
    assert len(folds) == 18
    assert folds[0].train_dates == DEVELOPMENT_DATES[:6]
    assert folds[0].validation_date == "2024-07-01"
    assert folds[-1].train_dates == DEVELOPMENT_DATES[17:23]
    assert folds[-1].validation_date == "2025-12-01"
    assert all(len(fold.train_dates) == 6 for fold in folds)


def test_fold_validation_rejects_future_or_non_chronological_dates() -> None:
    with pytest.raises(ValueError, match="2026"):
        validate_development_dates((*DEVELOPMENT_DATES[:-1], "2026-01-01"))
    with pytest.raises(ValueError, match="strictly precede"):
        validate_folds(
            [
                WalkForwardFold(
                    fold_id=1,
                    window="expanding",
                    train_dates=("2024-01-01", "2024-08-01"),
                    validation_date="2024-07-01",
                )
            ]
        )
    with pytest.raises(ValueError, match="chronological"):
        validate_folds(
            [
                WalkForwardFold(1, "expanding", ("2024-01-01",), "2024-08-01"),
                WalkForwardFold(2, "expanding", ("2024-01-01",), "2024-07-01"),
            ]
        )


def test_frozen_feature_sets_and_hyperparameters() -> None:
    validate_walkforward_feature_sets()
    validate_lightgbm_params()
    assert WALKFORWARD_FEATURE_SETS["extended_book_flow"] == [
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
    ]
    changed = dict(LIGHTGBM_PARAMS)
    changed["n_estimators"] = 121
    with pytest.raises(ValueError, match="frozen"):
        validate_lightgbm_params(changed)


def test_deterministic_1s_anchor_selection() -> None:
    mask = anchored_mask(31)
    assert ANCHOR_STRIDE == 10
    assert ANCHOR_OFFSET == 0
    assert mask.tolist() == [index in {0, 10, 20, 30} for index in range(31)]


def test_future_derived_columns_are_rejected() -> None:
    for column in [
        "ret_fwd_1s",
        "direction_1s",
        "future_mid_move_bps_1s",
        "future_move_in_spreads_1s",
        "next_mid_change_direction",
        "time_to_next_mid_change_ms",
        "target_time_1s",
        "actual_label_time_1s",
        "label_delay_ms_1s",
    ]:
        with pytest.raises(ValueError, match="Future/target-derived"):
            validate_model_columns(["qi_1", column])


def test_delta_and_sign_test_calculations() -> None:
    metrics = pd.DataFrame(
        [
            {
                "window": "expanding",
                "validation_date": "2024-07-01",
                "model": model,
                "spearman_ic": ic,
            }
            for model, ic in [
                ("qi_direct_baseline", 0.10),
                ("lightgbm_qi_ofi", 0.12),
                ("lightgbm_extended", 0.13),
            ]
        ]
    )
    deltas = fold_delta_rows(metrics, window="expanding")
    assert deltas.loc[0, "delta_ic_qi_ofi"] == pytest.approx(0.02)
    assert deltas.loc[0, "delta_ic_extended"] == pytest.approx(0.03)
    assert deltas.loc[0, "extended_increment"] == pytest.approx(0.01)
    signs = sign_test(pd.Series([0.1, -0.2, 0.0, 0.3]))
    assert signs["positive_folds"] == 2
    assert signs["negative_folds"] == 1
    assert signs["zero_folds"] == 1
    assert signs["sign_test_trials"] == 3
    summary = delta_summary(pd.Series([0.1, 0.2, -0.1]))
    assert summary["mean_delta_ic"] == pytest.approx(0.0666666667)
    assert np.isfinite(summary["fold_t_stat"])


def test_fold_local_preprocessing_contract_with_phase8_preprocessor() -> None:
    from microalpha.research.phase8 import TrainOnlyPreprocessor

    train = pd.DataFrame({"qi_1": [1.0, 2.0, np.nan]})
    validation = pd.DataFrame({"qi_1": [100.0, np.nan]})
    pre = TrainOnlyPreprocessor().fit(train, ["qi_1"])
    transformed = pre.transform(validation)
    assert pre.medians["qi_1"] == 1.5
    assert transformed.shape == (2, 2)
    assert "qi_1_missing" in pre.output_features


def test_deterministic_compact_result_hash(tmp_path) -> None:
    for file in [
        "walkforward_metrics.csv",
        "incremental_lift.csv",
        "window_comparison.csv",
        "prediction_correlations.csv",
        "feature_importance_by_fold.csv",
        "negative_control.csv",
        "README.md",
    ]:
        (tmp_path / file).write_text("a,b\n1,2\n", encoding="utf-8")
    (tmp_path / "phase9_summary.json").write_text(
        '{"phase9_results_hash": "ignored", "x": 1}', encoding="utf-8"
    )
    first = deterministic_results_hash(tmp_path)
    second = deterministic_results_hash(tmp_path)
    assert first == second
    assert PHASE9_PLAN_HASH == "4b1f0f0dd9f638ff4b5f40af04e17e8fc7753c4a500cac650d2537d3d40fb2c4"
