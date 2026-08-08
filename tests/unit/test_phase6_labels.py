import csv
import math
from decimal import Decimal
from pathlib import Path

from microalpha.features.engineering import FeatureConfig, build_feature_table
from microalpha.labels.generation import (
    LabelConfig,
    build_label_table,
    classify_return,
    summarize_label_file,
)
from microalpha.research.dataset import dataset_hash


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def fixed_fields() -> list[str]:
    return [
        "instrument",
        "observation_time",
        "feature_cutoff_time",
        "is_available",
        "book_event_time",
        "book_observation_time",
        "book_source_row_number",
        "best_bid",
        "bid_sz_1",
        "best_ask",
        "ask_sz_1",
        "mid",
        "spread",
        "latest_trade_event_time",
        "latest_trade_observation_time",
    ]


def state_fields() -> list[str]:
    return [
        "instrument",
        "observation_time",
        "feature_cutoff_time",
        "book_event_time",
        "book_observation_time",
        "book_source_row_number",
        "best_bid",
        "bid_size",
        "best_ask",
        "ask_size",
        "mid",
        "spread",
        "bid_depth",
        "ask_depth",
        "bid_px_1",
        "bid_sz_1",
        "ask_px_1",
        "ask_sz_1",
    ]


def make_row(
    time: str,
    mid: str = "100",
    *,
    spread: str = "2",
    available: str = "true",
    event_time: str | None = None,
    source_row: str = "1",
) -> dict[str, str]:
    mid_value = Decimal(mid)
    half_spread = Decimal(spread) / Decimal("2")
    bid = mid_value - half_spread
    ask = mid_value + half_spread
    event = event_time or time
    return {
        "instrument": "BTC-USDT",
        "observation_time": time,
        "feature_cutoff_time": time,
        "is_available": available,
        "book_event_time": event,
        "book_observation_time": time,
        "book_source_row_number": source_row,
        "best_bid": str(bid),
        "bid_sz_1": "10",
        "best_ask": str(ask),
        "ask_sz_1": "10",
        "mid": mid,
        "spread": spread,
        "latest_trade_event_time": "",
        "latest_trade_observation_time": "",
    }


def make_state(time: str, mid: str = "100", row: str = "1") -> dict[str, str]:
    fixed = make_row(time, mid=mid, source_row=row)
    fixed.update({
        "bid_size": fixed["bid_sz_1"],
        "ask_size": fixed["ask_sz_1"],
        "bid_depth": fixed["bid_sz_1"],
        "ask_depth": fixed["ask_sz_1"],
        "bid_px_1": fixed["best_bid"],
        "ask_px_1": fixed["best_ask"],
    })
    return fixed


def write_trades(path: Path) -> None:
    write_csv(
        path,
        [
            "source_row_number",
            "event_time",
            "receive_time",
            "price",
            "quantity",
            "side",
            "trade_id",
        ],
        [],
    )


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def build_labels(tmp_path: Path, rows: list[dict[str, str]], config: LabelConfig) -> Path:
    fixed = tmp_path / "fixed.csv"
    labels = tmp_path / "labels.csv"
    write_csv(fixed, fixed_fields(), rows)
    build_label_table(research_path=fixed, output_path=labels, config=config)
    return labels


def test_exact_return_and_exact_target_time(tmp_path: Path) -> None:
    t0 = "2026-01-02T10:00:00.000000+00:00"
    t1 = "2026-01-02T10:00:01.000000+00:00"
    labels = build_labels(
        tmp_path,
        [make_row(t0, "100"), make_row(t1, "101", source_row="2")],
        LabelConfig(horizons_ms=(1000,)),
    )
    row = read_rows(labels)[0]
    assert math.isclose(float(row["ret_fwd_1s"]), math.log(101 / 100))
    assert row["target_time_1s"] == t1
    assert row["actual_label_time_1s"] == t1
    assert row["label_delay_ms_1s"] == "0"


def test_first_observation_after_target_never_before(tmp_path: Path) -> None:
    t0 = "2026-01-02T10:00:00.000000+00:00"
    before = "2026-01-02T10:00:00.999000+00:00"
    after = "2026-01-02T10:00:01.002000+00:00"
    labels = build_labels(
        tmp_path,
        [make_row(t0, "100"), make_row(before, "120"), make_row(after, "101")],
        LabelConfig(horizons_ms=(1000,), max_label_delay_ms=10),
    )
    row = read_rows(labels)[0]
    assert row["actual_label_time_1s"] == after
    assert row["label_delay_ms_1s"] == "2"
    assert math.isclose(float(row["ret_fwd_1s"]), math.log(101 / 100))


def test_delay_tolerance_and_end_of_data_missing(tmp_path: Path) -> None:
    t0 = "2026-01-02T10:00:00.000000+00:00"
    late = "2026-01-02T10:00:02.000000+00:00"
    labels = build_labels(
        tmp_path,
        [make_row(t0, "100"), make_row(late, "101")],
        LabelConfig(horizons_ms=(100,), max_label_delay_ms=100),
    )
    rows = read_rows(labels)
    assert rows[0]["ret_fwd_100ms"] == ""
    assert rows[0]["direction_100ms"] == ""
    assert rows[1]["ret_fwd_100ms"] == ""
    assert len(rows) == 2


def test_classification_boundaries() -> None:
    assert classify_return(Decimal("0.00005"), Decimal("0.5")) == "FLAT"
    assert classify_return(Decimal("0.000050001"), Decimal("0.5")) == "UP"
    assert classify_return(Decimal("-0.00005"), Decimal("0.5")) == "FLAT"
    assert classify_return(Decimal("-0.000050001"), Decimal("0.5")) == "DOWN"


def test_feature_label_isolation_and_future_mutation_asymmetry(tmp_path: Path) -> None:
    t0 = "2026-01-02T10:00:00.000000+00:00"
    t1 = "2026-01-02T10:00:01.000000+00:00"
    fixed = tmp_path / "fixed.csv"
    states = tmp_path / "states.csv"
    trades = tmp_path / "trades.csv"
    features_1 = tmp_path / "features1.csv"
    features_2 = tmp_path / "features2.csv"
    labels_1 = tmp_path / "labels1.csv"
    labels_2 = tmp_path / "labels2.csv"
    write_trades(trades)
    write_csv(fixed, fixed_fields(), [make_row(t0, "100")])
    write_csv(states, state_fields(), [make_state(t0, "100", "1"), make_state(t1, "101", "2")])
    build_feature_table(
        fixed_clock_path=fixed,
        event_state_path=states,
        trades_path=trades,
        output_path=features_1,
        config=FeatureConfig(ofi_windows_ms=(1000,), trade_windows_ms=(1000,)),
    )
    all_fixed = tmp_path / "all_fixed.csv"
    write_csv(all_fixed, fixed_fields(), [make_row(t0, "100"), make_row(t1, "101", source_row="2")])
    build_label_table(
        research_path=all_fixed,
        output_path=labels_1,
        config=LabelConfig(horizons_ms=(1000,)),
    )

    write_csv(states, state_fields(), [make_state(t0, "100", "1"), make_state(t1, "500", "2")])
    build_feature_table(
        fixed_clock_path=fixed,
        event_state_path=states,
        trades_path=trades,
        output_path=features_2,
        config=FeatureConfig(ofi_windows_ms=(1000,), trade_windows_ms=(1000,)),
    )
    write_csv(all_fixed, fixed_fields(), [make_row(t0, "100"), make_row(t1, "500", source_row="2")])
    build_label_table(
        research_path=all_fixed,
        output_path=labels_2,
        config=LabelConfig(horizons_ms=(1000,)),
    )

    assert read_rows(features_1) == read_rows(features_2)
    assert read_rows(labels_1)[0]["ret_fwd_1s"] != read_rows(labels_2)[0]["ret_fwd_1s"]
    for path in Path("src/microalpha/features").glob("*.py"):
        assert "microalpha.labels" not in path.read_text(encoding="utf-8")


def test_monotonic_time_no_event_time_resort_and_unavailable_future_skip(
    tmp_path: Path,
) -> None:
    t0 = "2026-01-02T10:00:00.000000+00:00"
    unavailable = "2026-01-02T10:00:01.000000+00:00"
    after = "2026-01-02T10:00:01.050000+00:00"
    labels = build_labels(
        tmp_path,
        [
            make_row(t0, "100", event_time="2026-01-02T10:00:09.000000+00:00"),
            make_row(
                unavailable,
                "900",
                available="false",
                event_time="2026-01-02T09:59:59.000000+00:00",
            ),
            make_row(after, "101", event_time="2026-01-02T09:59:58.000000+00:00"),
        ],
        LabelConfig(horizons_ms=(1000,), max_label_delay_ms=100),
    )
    row = read_rows(labels)[0]
    assert row["actual_label_time_1s"] == after
    assert row["label_delay_ms_1s"] == "50"
    assert row["actual_label_time_1s"] > row["target_time_1s"]


def test_invalid_current_state_has_no_labels_and_next_change_unavailable(tmp_path: Path) -> None:
    t0 = "2026-01-02T10:00:00.000000+00:00"
    t1 = "2026-01-02T10:00:01.000000+00:00"
    labels = build_labels(
        tmp_path,
        [make_row(t0, "100", available="false"), make_row(t1, "101")],
        LabelConfig(horizons_ms=(1000,)),
    )
    row = read_rows(labels)[0]
    assert row["ret_fwd_1s"] == ""
    assert row["direction_1s"] == ""
    assert row["next_mid_change_available"] == "false"
    assert row["next_mid_change_direction"] == ""
    assert row["next_mid_change_direction"] not in {"-1", "0", "1"}


def test_no_next_mid_change_within_horizon_is_unavailable_not_neutral(tmp_path: Path) -> None:
    rows = [
        make_row("2026-01-02T10:00:00.000000+00:00", "100"),
        make_row("2026-01-02T10:00:00.100000+00:00", "100"),
        make_row("2026-01-02T10:00:00.200000+00:00", "100"),
    ]
    labels = build_labels(
        tmp_path,
        rows,
        LabelConfig(horizons_ms=(100,), next_mid_change_max_search_ms=200),
    )
    row = read_rows(labels)[0]
    assert row["next_mid_change_available"] == "false"
    assert row["next_mid_change_direction"] == ""
    assert row["time_to_next_mid_change_ms"] == ""
    assert row["next_mid_change_direction"] not in {"-1", "0", "1"}


def test_next_mid_change_available_for_valid_move(tmp_path: Path) -> None:
    rows = [
        make_row("2026-01-02T10:00:00.000000+00:00", "100"),
        make_row("2026-01-02T10:00:00.100000+00:00", "100"),
        make_row("2026-01-02T10:00:00.200000+00:00", "101"),
    ]
    labels = build_labels(
        tmp_path,
        rows,
        LabelConfig(horizons_ms=(100,), next_mid_change_max_search_ms=200),
    )
    row = read_rows(labels)[0]
    assert row["next_mid_change_available"] == "true"
    assert row["next_mid_change_direction"] == "1"
    assert row["time_to_next_mid_change_ms"] == "200"


def test_deterministic_output_and_summary(tmp_path: Path) -> None:
    rows = [
        make_row("2026-01-02T10:00:00.000000+00:00", "100"),
        make_row("2026-01-02T10:00:00.100000+00:00", "100"),
        make_row("2026-01-02T10:00:00.200000+00:00", "101"),
    ]
    fixed = tmp_path / "fixed.csv"
    out1 = tmp_path / "labels1.csv"
    out2 = tmp_path / "labels2.csv"
    write_csv(fixed, fixed_fields(), rows)
    config = LabelConfig(horizons_ms=(100,))
    build_label_table(research_path=fixed, output_path=out1, config=config)
    build_label_table(research_path=fixed, output_path=out2, config=config)
    assert dataset_hash(out1) == dataset_hash(out2)
    assert summarize_label_file(out1, config) == summarize_label_file(out2, config)


def test_cross_session_labels_disabled_by_default(tmp_path: Path) -> None:
    labels = build_labels(
        tmp_path,
        [
            make_row("2026-01-02T23:59:59.900000+00:00", "100"),
            make_row("2026-01-03T00:00:00.000000+00:00", "101", source_row="2"),
        ],
        LabelConfig(horizons_ms=(100,)),
    )
    row = read_rows(labels)[0]
    assert row["target_time_100ms"] == "2026-01-03T00:00:00.000000+00:00"
    assert row["ret_fwd_100ms"] == ""
    assert row["direction_100ms"] == ""
