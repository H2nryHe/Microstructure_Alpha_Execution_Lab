from pathlib import Path

import pytest

from microalpha.data.schemas import DataValidationError, validate_csv_rows

FIXTURES = Path("tests/fixtures/phase1")


def test_trade_schema_accepts_required_columns_and_types() -> None:
    row_count = validate_csv_rows(FIXTURES / "btc_usdt_trades_2026-01-02.csv", "trades")

    assert row_count == 3


def test_book_update_schema_accepts_required_columns_and_numeric_sequence() -> None:
    row_count = validate_csv_rows(
        FIXTURES / "btc_usdt_book_updates_2026-01-02.csv",
        "book_updates",
    )

    assert row_count == 3


def test_schema_rejects_missing_required_columns() -> None:
    with pytest.raises(DataValidationError, match="Missing required columns"):
        validate_csv_rows(FIXTURES / "trades_missing_required_column.csv", "trades")


def test_type_validation_rejects_non_positive_prices() -> None:
    with pytest.raises(DataValidationError, match="must be > 0"):
        validate_csv_rows(FIXTURES / "trades_invalid_type.csv", "trades")


def test_type_validation_rejects_non_integer_sequence_ids() -> None:
    with pytest.raises(DataValidationError, match="must be an integer"):
        validate_csv_rows(FIXTURES / "book_updates_invalid_sequence.csv", "book_updates")
