import csv
import math
from decimal import Decimal
from pathlib import Path

from microalpha.features.engineering import (
    FeatureConfig,
    build_feature_table,
    compute_ofi_events,
    depth_imbalance,
    microprice,
    ofi_event,
    queue_imbalance,
)
from microalpha.research.dataset import dataset_hash


def bbo(row, bid, bid_size, ask, ask_size):
    from datetime import datetime, timezone

    return type("BBO", (), {
        "observation_time": datetime(2026, 1, 2, tzinfo=timezone.utc),
        "source_row_number": row,
        "best_bid": Decimal(bid),
        "bid_sz_1": Decimal(bid_size),
        "best_ask": Decimal(ask),
        "ask_sz_1": Decimal(ask_size),
    })()


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def state_fields(depth=2):
    fields = [
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
    ]
    for i in range(1, depth + 1):
        fields += [f"bid_px_{i}", f"bid_sz_{i}"]
    for i in range(1, depth + 1):
        fields += [f"ask_px_{i}", f"ask_sz_{i}"]
    return fields


def fixed_fields(depth=2):
    fields = [
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
    for i in range(1, depth + 1):
        fields += [f"bid_px_{i}", f"bid_sz_{i}"]
    for i in range(1, depth + 1):
        fields += [f"ask_px_{i}", f"ask_sz_{i}"]
    return fields


def make_state(time, bid="100", bid_size="10", ask="102", ask_size="14", row="1"):
    mid = (Decimal(bid) + Decimal(ask)) / Decimal("2")
    spread = Decimal(ask) - Decimal(bid)
    return {
        "instrument": "BTC-USDT",
        "observation_time": time,
        "feature_cutoff_time": time,
        "book_event_time": time,
        "book_observation_time": time,
        "book_source_row_number": row,
        "best_bid": bid,
        "bid_size": bid_size,
        "best_ask": ask,
        "ask_size": ask_size,
        "mid": str(mid),
        "spread": str(spread),
        "bid_depth": bid_size,
        "ask_depth": ask_size,
        "bid_px_1": bid,
        "bid_sz_1": bid_size,
        "bid_px_2": str(Decimal(bid) - 1),
        "bid_sz_2": "5",
        "ask_px_1": ask,
        "ask_sz_1": ask_size,
        "ask_px_2": str(Decimal(ask) + 1),
        "ask_sz_2": "7",
    }


def make_fixed(time, state_time=None, available="true"):
    state_time = state_time or time
    row = make_state(state_time)
    row.update({
        "observation_time": time,
        "feature_cutoff_time": time,
        "is_available": available,
        "latest_trade_event_time": "",
        "latest_trade_observation_time": "",
    })
    return row


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
    write_csv(
        path,
        fieldnames,
        rows,
    )


def read_feature_rows(path: Path):
    with path.open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def test_state_feature_formulas_exact() -> None:
    bid_size = Decimal("10")
    ask_size = Decimal("14")
    assert queue_imbalance(bid_size, ask_size) == Decimal("-4") / Decimal("24")
    assert depth_imbalance(Decimal("15"), Decimal("21")) == Decimal("-6") / Decimal("36")
    mp = microprice(Decimal("100"), bid_size, Decimal("102"), ask_size)
    expected = Decimal("102") * Decimal("10") / Decimal("24")
    expected += Decimal("100") * Decimal("14") / Decimal("24")
    assert mp == expected
    mid = Decimal("101")
    assert abs((mp - mid) - (Decimal("-1") / Decimal("6"))) < Decimal("1e-25")


def test_all_ofi_transition_cases_exact() -> None:
    prev = bbo(1, "100", "10", "102", "12")
    cases = [
        (bbo(2, "100", "15", "102", "12"), Decimal("5")),
        (bbo(2, "100", "7", "102", "12"), Decimal("-3")),
        (bbo(2, "101", "8", "102", "12"), Decimal("8")),
        (bbo(2, "99", "8", "102", "12"), Decimal("-10")),
        (bbo(2, "100", "10", "102", "20"), Decimal("-8")),
        (bbo(2, "100", "10", "102", "5"), Decimal("7")),
        (bbo(2, "100", "10", "101", "9"), Decimal("-9")),
        (bbo(2, "100", "10", "103", "9"), Decimal("12")),
    ]
    for current, expected in cases:
        assert ofi_event(prev, current) == expected


def test_ranges_and_mirror_symmetry() -> None:
    assert Decimal("-1") <= queue_imbalance(Decimal("1"), Decimal("3")) <= Decimal("1")
    assert Decimal("-1") <= depth_imbalance(Decimal("4"), Decimal("2")) <= Decimal("1")
    mirrored_qi = queue_imbalance(Decimal("3"), Decimal("1"))
    assert mirrored_qi == -queue_imbalance(Decimal("1"), Decimal("3"))
    mirrored_di = depth_imbalance(Decimal("2"), Decimal("4"))
    assert mirrored_di == -depth_imbalance(Decimal("4"), Decimal("2"))
    left = microprice(Decimal("100"), Decimal("1"), Decimal("102"), Decimal("3"))
    right = microprice(Decimal("100"), Decimal("3"), Decimal("102"), Decimal("1"))
    assert (left - Decimal("101")) == -(right - Decimal("101"))
    assert ofi_event(bbo(1, "100", "1", "102", "3"), bbo(2, "101", "2", "102", "3")) == -ofi_event(
        bbo(1, "100", "3", "102", "1"),
        bbo(2, "100", "3", "101", "2"),
    )


def test_zero_denominators_and_missing_depth_policy(tmp_path: Path) -> None:
    assert queue_imbalance(Decimal("0"), Decimal("0")).is_nan()
    assert depth_imbalance(Decimal("0"), Decimal("0")).is_nan()
    assert microprice(Decimal("100"), Decimal("0"), Decimal("102"), Decimal("0")).is_nan()

    states = tmp_path / "states.csv"
    fixed = tmp_path / "fixed.csv"
    trades = tmp_path / "trades.csv"
    out = tmp_path / "features.csv"
    t = "2026-01-02T10:00:00.000000+00:00"
    row = make_fixed(t)
    row.pop("bid_sz_2")
    row.pop("ask_sz_2")
    write_csv(fixed, fixed_fields(), [row])
    write_csv(states, state_fields(), [make_state(t)])
    write_trades(trades, [])

    build_feature_table(
        fixed_clock_path=fixed,
        event_state_path=states,
        trades_path=trades,
        output_path=out,
        config=FeatureConfig(
            depth_levels=(5, 10),
            ofi_windows_ms=(1000,),
            trade_windows_ms=(1000,),
        ),
    )

    feature_row = read_feature_rows(out)[0]
    assert Decimal(feature_row["bid_depth_5"]) == Decimal("10")
    assert Decimal(feature_row["ask_depth_5"]) == Decimal("14")
    assert Decimal(feature_row["bid_depth_10"]) == Decimal("10")
    assert Decimal(feature_row["ask_depth_10"]) == Decimal("14")
    assert Decimal("-1") <= Decimal(feature_row["di_5"]) <= Decimal("1")
    assert Decimal(feature_row["spread"]) >= Decimal("0")


def test_trade_flow_formulas_and_ranges(tmp_path: Path) -> None:
    states = tmp_path / "states.csv"
    fixed = tmp_path / "fixed.csv"
    trades = tmp_path / "trades.csv"
    out = tmp_path / "features.csv"
    t0 = "2026-01-02T10:00:00.000000+00:00"
    cutoff = "2026-01-02T10:00:01.000000+00:00"
    write_csv(states, state_fields(), [make_state(t0), make_state(cutoff, row="2")])
    write_csv(fixed, fixed_fields(), [make_fixed(cutoff, state_time=cutoff)])
    write_trades(
        trades,
        [
            {
                "source_row_number": "1",
                "event_time": "2026-01-02T10:00:00.000000+00:00",
                "receive_time": "2026-01-02T10:00:00.000000+00:00",
                "price": "100",
                "quantity": "99",
                "side": "buy",
                "trade_id": "left_boundary",
            },
            {
                "source_row_number": "2",
                "event_time": "2026-01-02T10:00:00.500000+00:00",
                "receive_time": "2026-01-02T10:00:00.500000+00:00",
                "price": "100",
                "quantity": "4",
                "side": "buy",
                "trade_id": "buy_inside",
            },
            {
                "source_row_number": "3",
                "event_time": cutoff,
                "receive_time": cutoff,
                "price": "102",
                "quantity": "1",
                "side": "sell",
                "trade_id": "sell_at_cutoff",
            },
        ],
    )

    build_feature_table(
        fixed_clock_path=fixed,
        event_state_path=states,
        trades_path=trades,
        output_path=out,
        config=FeatureConfig(ofi_windows_ms=(1000,), trade_windows_ms=(1000,)),
    )

    row = read_feature_rows(out)[0]
    assert row["trade_count_1s"] == "2"
    assert Decimal(row["buy_volume_1s"]) == Decimal("4")
    assert Decimal(row["sell_volume_1s"]) == Decimal("1")
    assert Decimal(row["trade_volume_1s"]) == Decimal("5")
    assert Decimal(row["buy_notional_1s"]) == Decimal("400")
    assert Decimal(row["sell_notional_1s"]) == Decimal("102")
    assert Decimal(row["trade_notional_1s"]) == Decimal("502")
    assert Decimal(row["signed_trade_volume_1s"]) == Decimal("3")
    assert Decimal(row["trade_imbalance_1s"]) == Decimal("0.6")
    assert Decimal("-1") <= Decimal(row["trade_imbalance_1s"]) <= Decimal("1")


def test_window_boundary_and_trade_leakage(tmp_path: Path) -> None:
    states = tmp_path / "states.csv"
    fixed = tmp_path / "fixed.csv"
    trades = tmp_path / "trades.csv"
    out = tmp_path / "features.csv"
    t0 = "2026-01-02T10:00:00.000000+00:00"
    t1 = "2026-01-02T10:00:00.100000+00:00"
    t_future = "2026-01-02T10:00:00.100001+00:00"
    write_csv(
        states,
        state_fields(),
        [
            make_state(t0, row="1"),
            make_state(t1, bid_size="12", ask_size="14", row="2"),
            make_state(t_future, bid_size="99", ask_size="1", row="3"),
        ],
    )
    write_csv(fixed, fixed_fields(), [make_fixed(t1)])
    write_trades(
        trades,
        [
            {
                "source_row_number": "1",
                "event_time": t0,
                "receive_time": t0,
                "price": "100",
                "quantity": "5",
                "side": "buy",
                "trade_id": "start",
            },
            {
                "source_row_number": "2",
                "event_time": t1,
                "receive_time": t1,
                "price": "100",
                "quantity": "7",
                "side": "sell",
                "trade_id": "at_cutoff",
            },
            {
                "source_row_number": "3",
                "event_time": t_future,
                "receive_time": t_future,
                "price": "100",
                "quantity": "99",
                "side": "buy",
                "trade_id": "future",
            },
            {
                "source_row_number": "4",
                "event_time": t1,
                "receive_time": t_future,
                "price": "100",
                "quantity": "101",
                "side": "buy",
                "trade_id": "late_receive",
            },
        ],
    )
    build_feature_table(
        fixed_clock_path=fixed,
        event_state_path=states,
        trades_path=trades,
        output_path=out,
        config=FeatureConfig(ofi_windows_ms=(100,), trade_windows_ms=(100,)),
    )

    row = read_feature_rows(out)[0]
    assert row["trade_count_100ms"] == "1"
    assert row["sell_volume_100ms"] == "7"
    assert row["buy_volume_100ms"] == "0"
    assert row["ofi_100ms"] == "2"


def test_same_observation_time_preserves_source_order(tmp_path: Path) -> None:
    states = tmp_path / "states.csv"
    fixed = tmp_path / "fixed.csv"
    trades = tmp_path / "trades.csv"
    out = tmp_path / "features.csv"
    t = "2026-01-02T10:00:00.000000+00:00"
    rows = [
        make_state(t, bid_size="10", ask_size="10", row="1"),
        make_state(t, bid_size="15", ask_size="10", row="2"),
        make_state(t, bid_size="11", ask_size="10", row="3"),
    ]
    write_csv(states, state_fields(), rows)
    write_csv(fixed, fixed_fields(), [make_fixed(t, state_time=t)])
    write_trades(trades, [])

    events = compute_ofi_events(rows)
    assert [event.source_row_number for event in events] == [1, 2, 3]
    assert [event.ofi_event for event in events] == [Decimal("0"), Decimal("5"), Decimal("-4")]

    build_feature_table(
        fixed_clock_path=fixed,
        event_state_path=states,
        trades_path=trades,
        output_path=out,
        config=FeatureConfig(ofi_windows_ms=(1000,), trade_windows_ms=(1000,)),
    )
    assert Decimal(read_feature_rows(out)[0]["ofi_1s"]) == Decimal("1")


def test_event_stream_ofi_not_sampled_difference(tmp_path: Path) -> None:
    states = tmp_path / "states.csv"
    fixed = tmp_path / "fixed.csv"
    trades = tmp_path / "trades.csv"
    out = tmp_path / "features.csv"
    rows = [
        make_state("2026-01-02T10:00:00.000000+00:00", bid_size="10", ask_size="10", row="1"),
        make_state("2026-01-02T10:00:00.030000+00:00", bid_size="15", ask_size="10", row="2"),
        make_state("2026-01-02T10:00:00.060000+00:00", bid_size="15", ask_size="5", row="3"),
        make_state("2026-01-02T10:00:00.090000+00:00", bid_size="10", ask_size="5", row="4"),
    ]
    cutoff = "2026-01-02T10:00:00.100000+00:00"
    write_csv(states, state_fields(), rows)
    write_csv(fixed, fixed_fields(), [make_fixed(cutoff, state_time=rows[-1]["observation_time"])])
    write_trades(trades, [])
    build_feature_table(
        fixed_clock_path=fixed,
        event_state_path=states,
        trades_path=trades,
        output_path=out,
        config=FeatureConfig(ofi_windows_ms=(100,), trade_windows_ms=(100,)),
    )
    ofi = Decimal(read_feature_rows(out)[0]["ofi_100ms"])
    sampled_diff = Decimal(rows[-1]["bid_sz_1"]) - Decimal(rows[0]["bid_sz_1"])
    assert ofi == Decimal("5")
    assert sampled_diff == Decimal("0")
    assert len(compute_ofi_events(rows)) == 4


def test_no_trade_window_and_stale_row_propagation(tmp_path: Path) -> None:
    states = tmp_path / "states.csv"
    fixed = tmp_path / "fixed.csv"
    trades = tmp_path / "trades.csv"
    out = tmp_path / "features.csv"
    t = "2026-01-02T10:00:00.000000+00:00"
    write_csv(states, state_fields(), [make_state(t)])
    stale = make_fixed(t, available="false")
    write_csv(fixed, fixed_fields(), [stale])
    write_trades(trades, [])
    build_feature_table(
        fixed_clock_path=fixed,
        event_state_path=states,
        trades_path=trades,
        output_path=out,
        config=FeatureConfig(ofi_windows_ms=(1000,), trade_windows_ms=(1000,)),
    )
    row = read_feature_rows(out)[0]
    assert row["qi_1"] == ""
    assert row["trade_count_1s"] == "0"
    assert row["trade_imbalance_1s"] == "NaN"


def test_momentum_realized_volatility_and_deterministic_hash(tmp_path: Path) -> None:
    states = tmp_path / "states.csv"
    fixed = tmp_path / "fixed.csv"
    trades = tmp_path / "trades.csv"
    out1 = tmp_path / "features1.csv"
    out2 = tmp_path / "features2.csv"
    rows = [
        make_state("2026-01-02T10:00:00.000000+00:00", bid="99", ask="101", row="1"),
        make_state("2026-01-02T10:00:00.500000+00:00", bid="100", ask="102", row="2"),
        make_state("2026-01-02T10:00:01.000000+00:00", bid="101", ask="103", row="3"),
    ]
    cutoff = rows[-1]["observation_time"]
    write_csv(states, state_fields(), rows)
    write_csv(fixed, fixed_fields(), [make_fixed(cutoff, state_time=cutoff)])
    write_trades(trades, [])
    config = FeatureConfig(
        ofi_windows_ms=(1000,),
        trade_windows_ms=(1000,),
        realized_vol_windows_ms=(1000,),
        momentum_windows_ms=(1000,),
    )
    build_feature_table(
        fixed_clock_path=fixed,
        event_state_path=states,
        trades_path=trades,
        output_path=out1,
        config=config,
    )
    build_feature_table(
        fixed_clock_path=fixed,
        event_state_path=states,
        trades_path=trades,
        output_path=out2,
        config=config,
    )

    row = read_feature_rows(out1)[0]
    expected_mom = math.log(Decimal("102") / Decimal("100"))
    expected_vol = math.sqrt(
        math.log(Decimal("101") / Decimal("100")) ** 2
        + math.log(Decimal("102") / Decimal("101")) ** 2
    )
    assert math.isclose(float(row["mom_1s"]), expected_mom)
    assert math.isclose(float(row["realized_vol_1s"]), expected_vol)
    assert dataset_hash(out1) == dataset_hash(out2)


def test_future_mutation_does_not_change_historical_features(tmp_path: Path) -> None:
    states = tmp_path / "states.csv"
    fixed = tmp_path / "fixed.csv"
    trades = tmp_path / "trades.csv"
    out1 = tmp_path / "features1.csv"
    out2 = tmp_path / "features2.csv"
    t0 = "2026-01-02T10:00:00.000000+00:00"
    t1 = "2026-01-02T10:00:00.500000+00:00"
    cutoff = "2026-01-02T10:00:01.000000+00:00"
    future = "2026-01-02T10:00:01.000001+00:00"
    state_rows = [
        make_state(t0, bid="99", ask="101", row="1"),
        make_state(t1, bid="100", ask="102", bid_size="12", row="2"),
        make_state(cutoff, bid="101", ask="103", ask_size="11", row="3"),
        make_state(future, bid="102", ask="104", bid_size="15", row="4"),
    ]
    fixed_rows = [make_fixed(t1, state_time=t1), make_fixed(cutoff, state_time=cutoff)]
    trade_rows = [
        {
            "source_row_number": "1",
            "event_time": t1,
            "receive_time": t1,
            "price": "101",
            "quantity": "2",
            "side": "buy",
            "trade_id": "inside",
        },
        {
            "source_row_number": "2",
            "event_time": future,
            "receive_time": future,
            "price": "999",
            "quantity": "100",
            "side": "sell",
            "trade_id": "future",
        },
    ]
    write_csv(states, state_fields(), state_rows)
    write_csv(fixed, fixed_fields(), fixed_rows)
    write_trades(trades, trade_rows)
    config = FeatureConfig(
        ofi_windows_ms=(1000,),
        trade_windows_ms=(1000,),
        realized_vol_windows_ms=(1000,),
        momentum_windows_ms=(1000,),
    )
    build_feature_table(
        fixed_clock_path=fixed,
        event_state_path=states,
        trades_path=trades,
        output_path=out1,
        config=config,
    )

    mutated_states = state_rows[:-1] + [
        make_state(future, bid="500", ask="501", bid_size="999", ask_size="1", row="4")
    ]
    mutated_trades = trade_rows[:-1] + [
        {
            "source_row_number": "2",
            "event_time": future,
            "receive_time": future,
            "price": "1",
            "quantity": "999",
            "side": "buy",
            "trade_id": "future_mutated",
        },
    ]
    write_csv(states, state_fields(), mutated_states)
    write_trades(trades, mutated_trades)
    build_feature_table(
        fixed_clock_path=fixed,
        event_state_path=states,
        trades_path=trades,
        output_path=out2,
        config=config,
    )

    assert read_feature_rows(out1) == read_feature_rows(out2)


def test_numeric_outputs_finite_unless_missing(tmp_path: Path) -> None:
    states = tmp_path / "states.csv"
    fixed = tmp_path / "fixed.csv"
    trades = tmp_path / "trades.csv"
    out = tmp_path / "features.csv"
    t = "2026-01-02T10:00:01.000000+00:00"
    write_csv(states, state_fields(), [make_state(t)])
    write_csv(fixed, fixed_fields(), [make_fixed(t)])
    write_trades(
        trades,
        [
            {
                "source_row_number": "1",
                "event_time": t,
                "receive_time": t,
                "price": "100",
                "quantity": "1",
                "side": "buy",
                "trade_id": "1",
            }
        ],
    )
    build_feature_table(
        fixed_clock_path=fixed,
        event_state_path=states,
        trades_path=trades,
        output_path=out,
        config=FeatureConfig(ofi_windows_ms=(1000,), trade_windows_ms=(1000,)),
    )

    ignored_text_columns = {
        "feature_version",
        "instrument",
        "observation_time",
        "feature_cutoff_time",
        "is_available",
        "book_observation_time",
        "book_event_time",
        "book_source_row_number",
        "latest_trade_observation_time",
        "latest_trade_event_time",
    }
    row = read_feature_rows(out)[0]
    for name, value in row.items():
        if name in ignored_text_columns or value in ("", "NaN"):
            continue
        assert math.isfinite(float(value)), name
