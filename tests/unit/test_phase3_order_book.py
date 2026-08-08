import csv
import gzip
from decimal import Decimal
from pathlib import Path
from typing import Optional

import pytest

from microalpha.book.replay import BookEvent, replay_bronze_book_csv, replay_events
from microalpha.book.state import BookStateError, OrderBook
from microalpha.data.vendor_adapters import ingest_tardis_incremental_l2_gzip

REAL_FIXTURES = Path("tests/fixtures/real_subsets")


def event(
    source_row_number: int,
    side: str,
    price: str,
    quantity: str,
    *,
    update_type: str = "set",
    receive_time: str = "2026-01-02T00:00:00.000000+00:00",
    sequence_id: Optional[int] = None,
) -> BookEvent:
    return BookEvent(
        source_row_number=source_row_number,
        event_time=receive_time,
        receive_time=receive_time,
        side=side,
        price=price_decimal(price),
        quantity=price_decimal(quantity),
        update_type=update_type,
        sequence_id=sequence_id,
    )


def price_decimal(value: str):
    return Decimal(value)


def test_deterministic_hand_built_book_state() -> None:
    book = OrderBook()
    book.apply_level("bid", price_decimal("99"), price_decimal("10"))
    book.apply_level("ask", price_decimal("101"), price_decimal("12"))
    book.apply_level("bid", price_decimal("100"), price_decimal("5"))

    snapshot = book.snapshot(depth=1)

    assert snapshot["best_bid"] == "100"
    assert snapshot["best_ask"] == "101"
    assert snapshot["mid"] == "100.5"
    assert snapshot["spread"] == "1"


def test_delete_level_removes_price() -> None:
    book = OrderBook()
    book.apply_level("bid", price_decimal("99"), price_decimal("10"))
    book.apply_level("bid", price_decimal("100"), price_decimal("5"))
    book.apply_level("ask", price_decimal("101"), price_decimal("12"))
    book.apply_level("bid", price_decimal("100"), price_decimal("0"))

    assert book.snapshot(depth=1)["best_bid"] == "99"


def test_new_best_bid_and_new_best_ask() -> None:
    book = OrderBook()
    book.apply_level("bid", price_decimal("99"), price_decimal("10"))
    book.apply_level("ask", price_decimal("101"), price_decimal("12"))
    book.apply_level("bid", price_decimal("100"), price_decimal("1"))
    book.apply_level("ask", price_decimal("100.5"), price_decimal("2"))

    snapshot = book.snapshot(depth=1)

    assert snapshot["best_bid"] == "100"
    assert snapshot["best_ask"] == "100.5"


def test_multi_level_depth_ordering() -> None:
    book = OrderBook()
    for price in ("98", "100", "99"):
        book.apply_level("bid", price_decimal(price), price_decimal("1"))
    for price in ("103", "101", "102"):
        book.apply_level("ask", price_decimal(price), price_decimal("2"))

    snapshot = book.snapshot(depth=3)

    assert [snapshot[f"bid_px_{index}"] for index in range(1, 4)] == ["100", "99", "98"]
    assert [snapshot[f"ask_px_{index}"] for index in range(1, 4)] == ["101", "102", "103"]


def test_reconstructed_state_crossed_book_detection() -> None:
    events = [
        event(1, "bid", "99", "1", update_type="snapshot"),
        event(2, "ask", "101", "1", update_type="snapshot"),
        event(3, "bid", "102", "1", receive_time="2026-01-02T00:00:01.000000+00:00"),
    ]

    with pytest.raises(BookStateError, match="Crossed or locked"):
        replay_events(events)


def test_pre_snapshot_updates_are_ignored() -> None:
    events = [
        event(1, "bid", "100", "1"),
        event(2, "ask", "101", "1"),
        event(
            3,
            "bid",
            "99",
            "2",
            update_type="snapshot",
            receive_time="2026-01-02T00:00:01+00:00",
        ),
        event(
            4,
            "ask",
            "101",
            "3",
            update_type="snapshot",
            receive_time="2026-01-02T00:00:01+00:00",
        ),
    ]

    stats = replay_events(events)

    assert stats.rows_ignored_before_snapshot == 2
    assert stats.final_state["best_bid"] == "99"


def test_same_local_timestamp_source_order_preservation() -> None:
    events = [
        event(1, "bid", "99", "1", update_type="snapshot"),
        event(2, "ask", "101", "1", update_type="snapshot"),
        event(3, "bid", "100", "1", receive_time="2026-01-02T00:00:01+00:00"),
        event(4, "bid", "100", "2", receive_time="2026-01-02T00:00:01+00:00"),
    ]

    stats = replay_events(events)

    assert stats.final_state["best_bid"] == "100"
    assert stats.final_state["bid_size"] == "2"


def test_deterministic_replay_output_hash() -> None:
    events = [
        event(1, "bid", "99", "1", update_type="snapshot"),
        event(2, "ask", "101", "1", update_type="snapshot"),
        event(3, "bid", "100", "2", receive_time="2026-01-02T00:00:01+00:00"),
    ]

    first = replay_events(events)
    second = replay_events(events)

    assert first.final_state == second.final_state
    assert first.output_hash == second.output_hash


def test_no_sequence_id_tardis_replay_mode() -> None:
    events = [
        event(1, "bid", "99", "1", update_type="snapshot"),
        event(2, "ask", "101", "1", update_type="snapshot"),
        event(3, "bid", "100", "2", receive_time="2026-01-02T00:00:01+00:00"),
    ]

    stats = replay_events(events, ordering_mode="capture_order")

    assert stats.final_state["best_bid"] == "100"


def test_vendor_sequence_fixture_replay() -> None:
    events = [
        event(1, "bid", "99", "1", update_type="snapshot", sequence_id=1),
        event(2, "ask", "101", "1", update_type="snapshot", sequence_id=1),
        event(3, "bid", "100", "2", sequence_id=2),
    ]

    stats = replay_events(events, ordering_mode="vendor_sequence")

    assert stats.final_state["best_bid"] == "100"


def _gzip_fixture(source_csv: Path, destination_gzip: Path) -> None:
    with source_csv.open("rb") as source_file:
        with destination_gzip.open("wb") as output_file:
            with gzip.GzipFile(fileobj=output_file, mode="wb", mtime=0) as gzip_file:
                gzip_file.write(source_file.read())


def test_real_tardis_contiguous_regression_fixture_replay(tmp_path: Path) -> None:
    source_csv = (
        REAL_FIXTURES
        / "tardis_binance_BTCUSDT_incremental_book_L2_2019-12-01_rows_1_2050.csv"
    )
    source_gzip = tmp_path / "BTCUSDT.csv.gz"
    _gzip_fixture(source_csv, source_gzip)
    result = ingest_tardis_incremental_l2_gzip(
        source_gzip_path=source_gzip,
        instrument="BTC-USDT",
        vendor_symbol="BTCUSDT",
        trade_date="2019-12-01",
        raw_dir=tmp_path / "raw",
        bronze_dir=tmp_path / "bronze",
        manifest_dir=tmp_path / "manifests",
    )

    with Path(result.bronze_path).open("r", encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))

    stats = replay_bronze_book_csv(result.bronze_path)

    assert len(rows) == 2050
    assert rows[0]["source_row_number"] == "1"
    assert stats.initial_snapshot_start_row == 1
    assert stats.initial_snapshot_end_row == 2000
    assert stats.rows_processed == 2050
    assert Decimal(stats.final_state["best_bid"]) < Decimal(stats.final_state["best_ask"])
    assert stats.invalid_states == 0
