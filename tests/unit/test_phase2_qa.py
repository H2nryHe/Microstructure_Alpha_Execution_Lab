from pathlib import Path

import pytest

from microalpha.data.qa import (
    ERROR,
    WARNING,
    QAContinuationError,
    assert_can_continue,
    load_qa_config,
    validate_market_data_csv,
)

FIXTURES = Path("tests/fixtures/phase2")
CONFIG = load_qa_config("configs/qa.yaml")


def validators(report):
    return {issue.validator for issue in report.issues}


def severities(report, validator):
    return {issue.severity for issue in report.issues if issue.validator == validator}


def test_clean_fixture_passes() -> None:
    report = validate_market_data_csv(
        FIXTURES / "clean_book.csv",
        dataset_type="book_updates",
        config=CONFIG,
    )

    assert report.status == "PASS"
    assert report.can_continue is True
    assert report.error_count == 0


@pytest.mark.parametrize(
    ("fixture", "dataset_type", "validator", "severity"),
    [
        ("missing_timestamp.csv", "trades", "missing_timestamp", ERROR),
        ("duplicate_trade.csv", "trades", "exact_duplicate", WARNING),
        ("backward_timestamp.csv", "trades", "backward_timestamp", ERROR),
        ("negative_quantity.csv", "trades", "negative_quantity", ERROR),
        ("zero_price.csv", "trades", "invalid_price", ERROR),
        ("crossed_book.csv", "book_updates", "crossed_book", ERROR),
        ("locked_book.csv", "book_updates", "locked_book", WARNING),
        ("sequence_gap.csv", "book_updates", "sequence_gap", ERROR),
        ("repeated_sequence.csv", "book_updates", "repeated_sequence_id", ERROR),
        ("out_of_order_sequence.csv", "book_updates", "out_of_order_sequence_id", ERROR),
        ("price_discontinuity.csv", "trades", "extreme_price_discontinuity", ERROR),
        ("size_outlier.csv", "trades", "extreme_size", WARNING),
        ("update_gap.csv", "trades", "update_gap", WARNING),
        ("stale_bbo.csv", "book_updates", "stale_bbo", WARNING),
    ],
)
def test_isolated_corruption_fixtures(fixture, dataset_type, validator, severity) -> None:
    report = validate_market_data_csv(FIXTURES / fixture, dataset_type=dataset_type, config=CONFIG)

    assert validator in validators(report)
    assert severity in severities(report, validator)


def test_combined_corruption_fixture_reports_multiple_validators() -> None:
    report = validate_market_data_csv(
        FIXTURES / "combined_corruption.csv",
        dataset_type="book_updates",
        config=CONFIG,
    )

    assert report.status == "FAIL"
    assert {"invalid_price", "negative_quantity", "sequence_gap", "crossed_book"} <= validators(
        report
    )
    assert report.warning_count >= 1


def test_qa_report_output_is_deterministic() -> None:
    first = validate_market_data_csv(
        FIXTURES / "combined_corruption.csv",
        dataset_type="book_updates",
        config=CONFIG,
    )
    second = validate_market_data_csv(
        FIXTURES / "combined_corruption.csv",
        dataset_type="book_updates",
        config=CONFIG,
    )

    assert first.to_json() == second.to_json()


def test_invalid_critical_data_blocks_downstream_continuation() -> None:
    report = validate_market_data_csv(
        FIXTURES / "zero_price.csv",
        dataset_type="trades",
        config=CONFIG,
    )

    with pytest.raises(QAContinuationError):
        assert_can_continue(report)
