import csv
import gzip
from pathlib import Path

from microalpha.book.replay import BookEvent
from microalpha.data.vendor_adapters import (
    ingest_tardis_incremental_l2_gzip,
    ingest_tardis_trades_gzip,
)
from microalpha.research.dataset import (
    ResearchConfig,
    audit_fixed_clock_rows,
    build_event_state_table,
    build_fixed_clock_table,
    dataset_hash,
    parse_iso_utc,
    read_trades,
)

REAL_FIXTURES = Path("tests/fixtures/real_subsets")


def dec(value: str):
    from decimal import Decimal

    return Decimal(value)


def event(row, event_time, receive_time, side, price, quantity, update_type="set"):
    return BookEvent(
        source_row_number=row,
        event_time=event_time,
        receive_time=receive_time,
        side=side,
        price=dec(price),
        quantity=dec(quantity),
        update_type=update_type,
    )


def write_trades(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = [
        "source_row_number",
        "event_time",
        "receive_time",
        "price",
        "quantity",
        "side",
        "trade_id",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def base_events():
    t0_exchange = "2026-01-02T09:59:59.000000+00:00"
    t0_observed = "2026-01-02T10:00:00.000000+00:00"
    t099 = "2026-01-02T10:00:00.099000+00:00"
    t101 = "2026-01-02T10:00:00.101000+00:00"
    return [
        event(1, t0_exchange, t0_observed, "bid", "99", "10", "snapshot"),
        event(2, t0_exchange, t0_observed, "ask", "101", "12", "snapshot"),
        event(3, t099, t099, "bid", "100", "5"),
        event(4, t101, t101, "ask", "102", "6"),
    ]


def test_sampling_boundary_and_exact_boundary(tmp_path: Path) -> None:
    states = tmp_path / "states.csv"
    trades = tmp_path / "trades.csv"
    fixed = tmp_path / "fixed.csv"
    config = ResearchConfig(depth=2, sampling_interval_ms=1, max_staleness_ms=1000)
    build_event_state_table(
        book_events=base_events(),
        output_path=states,
        instrument="BTC-USDT",
        config=config,
    )
    write_trades(trades, [])

    build_fixed_clock_table(
        event_state_path=states,
        trades_path=trades,
        output_path=fixed,
        config=config,
        start_time="2026-01-02T10:00:00.100000+00:00",
        end_time="2026-01-02T10:00:00.101000+00:00",
    )

    rows = read_rows(fixed)
    assert rows[0]["feature_cutoff_time"] == "2026-01-02T10:00:00.100000+00:00"
    assert rows[0]["book_observation_time"] == "2026-01-02T10:00:00.099000+00:00"
    assert rows[1]["feature_cutoff_time"] == "2026-01-02T10:00:00.101000+00:00"
    assert rows[1]["book_observation_time"] == "2026-01-02T10:00:00.101000+00:00"


def test_temporal_causality_and_trade_leakage(tmp_path: Path) -> None:
    states = tmp_path / "states.csv"
    trades = tmp_path / "trades.csv"
    fixed = tmp_path / "fixed.csv"
    config = ResearchConfig(depth=2, sampling_interval_ms=100, max_staleness_ms=1000)
    build_event_state_table(
        book_events=base_events(),
        output_path=states,
        instrument="BTC-USDT",
        config=config,
    )
    write_trades(
        trades,
        [
            {
                "source_row_number": "1",
                "event_time": "2026-01-02T10:00:00.050000+00:00",
                "receive_time": "2026-01-02T10:00:00.050000+00:00",
                "price": "100",
                "quantity": "1",
                "side": "buy",
                "trade_id": "t1",
            },
            {
                "source_row_number": "2",
                "event_time": "2026-01-02T10:00:00.100001+00:00",
                "receive_time": "2026-01-02T10:00:00.100001+00:00",
                "price": "101",
                "quantity": "1",
                "side": "sell",
                "trade_id": "future",
            },
        ],
    )
    build_fixed_clock_table(
        event_state_path=states,
        trades_path=trades,
        output_path=fixed,
        config=config,
        start_time="2026-01-02T10:00:00.100000+00:00",
        end_time="2026-01-02T10:00:00.100000+00:00",
    )

    row = read_rows(fixed)[0]
    cutoff = parse_iso_utc(row["feature_cutoff_time"])
    assert parse_iso_utc(row["book_observation_time"]) <= cutoff
    assert parse_iso_utc(row["latest_trade_observation_time"]) <= cutoff
    assert row["latest_trade_source_row_number"] == "1"


def test_staleness_marks_row_unavailable(tmp_path: Path) -> None:
    states = tmp_path / "states.csv"
    trades = tmp_path / "trades.csv"
    fixed = tmp_path / "fixed.csv"
    config = ResearchConfig(depth=2, sampling_interval_ms=100, max_staleness_ms=50)
    build_event_state_table(
        book_events=base_events()[:2],
        output_path=states,
        instrument="BTC-USDT",
        config=config,
    )
    write_trades(trades, [])
    build_fixed_clock_table(
        event_state_path=states,
        trades_path=trades,
        output_path=fixed,
        config=config,
        start_time="2026-01-02T10:00:00.100000+00:00",
        end_time="2026-01-02T10:00:00.100000+00:00",
    )

    row = read_rows(fixed)[0]
    assert row["is_available"] == "false"
    assert row["unavailable_reason"] == "stale_book_state"


def test_snapshot_boundary_no_state_before_initialized_snapshot(tmp_path: Path) -> None:
    states = tmp_path / "states.csv"
    stats = build_event_state_table(
        book_events=[
            event(1, "2026-01-02T10:00:00+00:00", "2026-01-02T10:00:00+00:00", "bid", "99", "1"),
            event(2, "2026-01-02T10:00:01+00:00", "2026-01-02T10:00:01+00:00", "ask", "101", "1"),
        ],
        output_path=states,
        instrument="BTC-USDT",
        config=ResearchConfig(depth=1),
    )

    assert stats.event_state_rows == 0
    assert read_rows(states) == []


def test_same_local_timestamp_group_waits_for_complete_update(tmp_path: Path) -> None:
    states = tmp_path / "states.csv"
    t0 = "2026-01-02T10:00:00+00:00"
    t1 = "2026-01-02T10:00:01+00:00"
    events = [
        event(1, t0, t0, "bid", "99", "1", "snapshot"),
        event(2, t0, t0, "ask", "101", "1", "snapshot"),
        event(3, t1, t1, "bid", "102", "1"),
        event(4, t1, t1, "ask", "101", "0"),
        event(5, t1, t1, "ask", "103", "1"),
    ]
    stats = build_event_state_table(
        book_events=events,
        output_path=states,
        instrument="BTC-USDT",
        config=ResearchConfig(depth=1),
    )

    rows = read_rows(states)
    assert stats.crossed_or_invalid_states == 0
    assert rows[-1]["best_bid"] == "102"
    assert rows[-1]["best_ask"] == "103"


def test_no_event_time_resort_preserves_local_source_order(tmp_path: Path) -> None:
    states = tmp_path / "states.csv"
    local0 = "2026-01-02T10:00:00+00:00"
    events = [
        event(1, "2026-01-02T10:00:02+00:00", local0, "bid", "99", "1", "snapshot"),
        event(2, "2026-01-02T10:00:01+00:00", local0, "ask", "101", "1", "snapshot"),
        event(3, "2026-01-02T09:59:59+00:00", "2026-01-02T10:00:01+00:00", "bid", "100", "1"),
    ]
    build_event_state_table(
        book_events=events,
        output_path=states,
        instrument="BTC-USDT",
        config=ResearchConfig(depth=1),
    )

    rows = read_rows(states)
    assert rows[-1]["book_source_row_number"] == "3"
    assert rows[-1]["book_event_time"] == "2026-01-02T09:59:59+00:00"


def test_future_mutation_does_not_change_rows_at_or_before_cutoff(tmp_path: Path) -> None:
    config = ResearchConfig(depth=2, sampling_interval_ms=100, max_staleness_ms=1000)
    trades = tmp_path / "trades.csv"
    write_trades(trades, [])

    def build(events, stem):
        states = tmp_path / f"{stem}_states.csv"
        fixed = tmp_path / f"{stem}_fixed.csv"
        build_event_state_table(
            book_events=events,
            output_path=states,
            instrument="BTC-USDT",
            config=config,
        )
        build_fixed_clock_table(
            event_state_path=states,
            trades_path=trades,
            output_path=fixed,
            config=config,
            start_time="2026-01-02T10:00:00.100000+00:00",
            end_time="2026-01-02T10:00:00.100000+00:00",
        )
        return read_rows(fixed)

    baseline = build(base_events(), "baseline")
    mutated = build(
        base_events()
        + [
            event(
                5,
                "2026-01-02T10:00:01+00:00",
                "2026-01-02T10:00:01+00:00",
                "bid",
                "50",
                "99",
            )
        ],
        "mutated",
    )

    assert baseline == mutated


def test_deterministic_research_dataset_hash(tmp_path: Path) -> None:
    config = ResearchConfig(depth=2)
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    build_event_state_table(
        book_events=base_events(),
        output_path=first,
        instrument="BTC-USDT",
        config=config,
    )
    build_event_state_table(
        book_events=base_events(),
        output_path=second,
        instrument="BTC-USDT",
        config=config,
    )

    assert dataset_hash(first) == dataset_hash(second)


def _gzip_fixture(source_csv: Path, destination_gzip: Path) -> None:
    with source_csv.open("rb") as source_file:
        with destination_gzip.open("wb") as output_file:
            with gzip.GzipFile(fileobj=output_file, mode="wb", mtime=0) as gzip_file:
                gzip_file.write(source_file.read())


def test_real_contiguous_phase4_regression_fixture(tmp_path: Path) -> None:
    l2_csv = (
        REAL_FIXTURES
        / "tardis_binance_BTCUSDT_incremental_book_L2_2019-12-01_rows_1_2050.csv"
    )
    trades_csv = REAL_FIXTURES / "tardis_binance_BTCUSDT_trades_2019-12-01_rows_1_100.csv"
    l2_gzip = tmp_path / "l2.csv.gz"
    trades_gzip = tmp_path / "trades.csv.gz"
    _gzip_fixture(l2_csv, l2_gzip)
    _gzip_fixture(trades_csv, trades_gzip)
    l2 = ingest_tardis_incremental_l2_gzip(
        source_gzip_path=l2_gzip,
        instrument="BTC-USDT",
        vendor_symbol="BTCUSDT",
        trade_date="2019-12-01",
        raw_dir=tmp_path / "raw",
        bronze_dir=tmp_path / "bronze",
        manifest_dir=tmp_path / "manifests",
    )
    trades = ingest_tardis_trades_gzip(
        source_gzip_path=trades_gzip,
        instrument="BTC-USDT",
        vendor_symbol="BTCUSDT",
        trade_date="2019-12-01",
        raw_dir=tmp_path / "raw",
        bronze_dir=tmp_path / "bronze",
        manifest_dir=tmp_path / "manifests",
    )
    states = tmp_path / "states.csv"
    fixed = tmp_path / "fixed.csv"
    config = ResearchConfig(depth=10, sampling_interval_ms=100, max_staleness_ms=1000)
    state_stats = build_event_state_table(
        book_events=read_bronze_events(l2.bronze_path),
        output_path=states,
        instrument="BTC-USDT",
        config=config,
    )
    fixed_stats = build_fixed_clock_table(
        event_state_path=states,
        trades_path=trades.bronze_path,
        output_path=fixed,
        config=config,
    )
    audits = audit_fixed_clock_rows(
        fixed_clock_path=fixed,
        event_state_path=states,
        trades_path=trades.bronze_path,
        sample_count=5,
    )

    assert state_stats.source_l2_rows == 2050
    assert state_stats.event_state_rows > 1
    assert fixed_stats.fixed_clock_rows > 0
    assert all(
        not audit["next_future_book_event_time"]
        or audit["selected_book_state_time"] <= audit["cutoff_time"]
        for audit in audits
    )
    assert read_trades(trades.bronze_path)[0].observation_time


def read_bronze_events(path):
    from microalpha.book.replay import read_bronze_book_events

    return read_bronze_book_events(path)
