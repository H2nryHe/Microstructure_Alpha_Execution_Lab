from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from microalpha.execution.simulator import (
    BookSnapshot,
    OrderRequest,
    TradePrint,
    artifact_hash,
    compute_markouts,
    execute_market_order,
    make_order_id,
    simulate_limit_order,
)


def ts(ms: int = 0) -> datetime:
    return datetime(2024, 7, 1, tzinfo=timezone.utc) + timedelta(milliseconds=ms)


def book(time_ms: int = 0) -> BookSnapshot:
    return BookSnapshot(
        observation_time=ts(time_ms),
        bids=((99.0, 2.0), (98.0, 3.0), (97.0, 5.0)),
        asks=((101.0, 2.0), (102.0, 3.0), (103.0, 5.0)),
    )


def order(
    *,
    side: str = "BUY",
    order_type: str = "MARKET",
    quantity: float = 1.0,
    arrival_ms: int = 0,
    limit_price: float | None = None,
    expiration_ms: int | None = None,
    cancel_effective_ms: int | None = None,
) -> OrderRequest:
    arrival = ts(arrival_ms)
    return OrderRequest(
        order_id=make_order_id(side, order_type, quantity, arrival_ms, limit_price),
        date="2024-07-01",
        signal_id="sig-1",
        model="unit",
        side=side,  # type: ignore[arg-type]
        order_type=order_type,  # type: ignore[arg-type]
        quantity=quantity,
        limit_price=limit_price,
        order_create_time=ts(0),
        order_arrival_time=arrival,
        remaining_quantity=quantity,
        expiration_time=ts(expiration_ms) if expiration_ms is not None else None,
        cancel_requested_time=ts(cancel_effective_ms - 50)
        if cancel_effective_ms is not None
        else None,
        cancel_effective_time=ts(cancel_effective_ms) if cancel_effective_ms is not None else None,
    )


def test_no_fill_before_arrival() -> None:
    request = order(arrival_ms=50, quantity=1.0)
    result, fills = execute_market_order(
        request,
        BookSnapshot(observation_time=ts(50), bids=book().bids, asks=book().asks),
        fee_bps=0.0,
        decision_mid=100.0,
    )
    assert result["status"] == "FILLED"
    assert all(fill.fill_time >= request.order_arrival_time for fill in fills)


def test_market_buy_fills_at_ask_not_bid() -> None:
    result, fills = execute_market_order(order(side="BUY"), book(), fee_bps=0.0)
    assert result["status"] == "FILLED"
    assert fills[0].price == 101.0


def test_market_sell_fills_at_bid_not_ask() -> None:
    result, fills = execute_market_order(order(side="SELL"), book(), fee_bps=0.0)
    assert result["status"] == "FILLED"
    assert fills[0].price == 99.0


def test_multilevel_buy_vwap() -> None:
    result, fills = execute_market_order(order(side="BUY", quantity=4.0), book(), fee_bps=0.0)
    assert [(fill.price, fill.quantity) for fill in fills] == [(101.0, 2.0), (102.0, 2.0)]
    assert result["vwap_fill_price"] == pytest.approx(101.5)
    assert result["levels_consumed"] == 2


def test_multilevel_sell_vwap() -> None:
    result, fills = execute_market_order(order(side="SELL", quantity=4.0), book(), fee_bps=0.0)
    assert [(fill.price, fill.quantity) for fill in fills] == [(99.0, 2.0), (98.0, 2.0)]
    assert result["vwap_fill_price"] == pytest.approx(98.5)


def test_insufficient_depth_records_residual() -> None:
    result, fills = execute_market_order(order(side="BUY", quantity=20.0), book(), fee_bps=0.0)
    assert sum(fill.quantity for fill in fills) == pytest.approx(10.0)
    assert result["remaining_quantity"] == pytest.approx(10.0)
    assert result["insufficient_depth"] is True


def test_passive_queue_depletes_before_fill() -> None:
    request = order(
        side="BUY",
        order_type="LIMIT",
        quantity=2.0,
        limit_price=99.0,
        arrival_ms=0,
        expiration_ms=1000,
    )
    arrival = BookSnapshot(observation_time=ts(0), bids=((99.0, 5.0),), asks=((101.0, 1.0),))
    trades = [
        TradePrint(ts(100), "sell", 99.0, 3.0),
        TradePrint(ts(200), "sell", 99.0, 2.0),
        TradePrint(ts(300), "sell", 99.0, 1.0),
    ]
    result, fills = simulate_limit_order(
        request,
        arrival,
        trades,
        fee_bps=0.0,
        queue_fraction=1.0,
        decision_mid=100.0,
    )
    assert result["status"] == "PARTIALLY_FILLED"
    assert len(fills) == 1
    assert fills[0].quantity == pytest.approx(1.0)
    assert fills[0].fill_time == ts(300)


def test_passive_partial_fill_remains_partially_filled() -> None:
    request = order(
        side="BUY",
        order_type="LIMIT",
        quantity=2.0,
        limit_price=99.0,
        expiration_ms=1000,
    )
    arrival = BookSnapshot(observation_time=ts(0), bids=((99.0, 0.0),), asks=((101.0, 1.0),))
    result, fills = simulate_limit_order(
        request,
        arrival,
        [TradePrint(ts(100), "sell", 99.0, 1.25)],
        fee_bps=0.0,
        queue_fraction=1.0,
        decision_mid=100.0,
    )
    assert result["status"] == "PARTIALLY_FILLED"
    assert result["remaining_quantity"] == pytest.approx(0.75)
    assert len(fills) == 1


def test_expiration_without_fill() -> None:
    request = order(
        side="BUY",
        order_type="LIMIT",
        quantity=1.0,
        limit_price=99.0,
        expiration_ms=100,
    )
    arrival = BookSnapshot(observation_time=ts(0), bids=((99.0, 0.0),), asks=((101.0, 1.0),))
    result, fills = simulate_limit_order(
        request,
        arrival,
        [TradePrint(ts(101), "sell", 99.0, 1.0)],
        fee_bps=0.0,
        queue_fraction=1.0,
        decision_mid=100.0,
    )
    assert result["status"] == "EXPIRED"
    assert fills == []


def test_cancel_prevents_later_fill() -> None:
    request = order(
        side="BUY",
        order_type="LIMIT",
        quantity=1.0,
        limit_price=99.0,
        expiration_ms=1000,
        cancel_effective_ms=100,
    )
    arrival = BookSnapshot(observation_time=ts(0), bids=((99.0, 0.0),), asks=((101.0, 1.0),))
    result, fills = simulate_limit_order(
        request,
        arrival,
        [TradePrint(ts(101), "sell", 99.0, 1.0)],
        fee_bps=0.0,
        queue_fraction=1.0,
        decision_mid=100.0,
    )
    assert result["status"] == "CANCELLED"
    assert fills == []


def test_fill_before_cancel_effective_is_allowed() -> None:
    request = order(
        side="BUY",
        order_type="LIMIT",
        quantity=1.0,
        limit_price=99.0,
        expiration_ms=1000,
        cancel_effective_ms=100,
    )
    arrival = BookSnapshot(observation_time=ts(0), bids=((99.0, 0.0),), asks=((101.0, 1.0),))
    result, fills = simulate_limit_order(
        request,
        arrival,
        [TradePrint(ts(99), "sell", 99.0, 1.0)],
        fee_bps=0.0,
        queue_fraction=1.0,
        decision_mid=100.0,
    )
    assert result["status"] == "FILLED"
    assert fills[0].fill_time == ts(99)


def test_same_timestamp_trade_is_not_eligible_for_passive_fill() -> None:
    request = order(side="BUY", order_type="LIMIT", quantity=1.0, limit_price=99.0)
    arrival = BookSnapshot(observation_time=ts(0), bids=((99.0, 0.0),), asks=((101.0, 1.0),))
    result, fills = simulate_limit_order(
        request,
        arrival,
        [TradePrint(ts(0), "sell", 99.0, 1.0), TradePrint(ts(1), "sell", 99.0, 1.0)],
        fee_bps=0.0,
        queue_fraction=1.0,
        decision_mid=100.0,
    )
    assert result["status"] == "FILLED"
    assert fills[0].fill_time == ts(1)


def test_fee_calculation() -> None:
    _result, fills = execute_market_order(order(side="BUY", quantity=1.0), book(), fee_bps=1.0)
    assert fills[0].fee_quote == pytest.approx(0.0101)


def test_markout_sign() -> None:
    _result, buy_fills = execute_market_order(order(side="BUY"), book(), fee_bps=0.0)
    _result, sell_fills = execute_market_order(order(side="SELL"), book(), fee_bps=0.0)
    future = [
        book(0),
        BookSnapshot(observation_time=ts(100), bids=((101.0, 1.0),), asks=((103.0, 1.0),)),
    ]
    rows = compute_markouts([buy_fills[0], sell_fills[0]], future, [100])
    assert rows[0]["signed_markout_100ms"] > 0
    assert rows[1]["signed_markout_100ms"] < 0


def test_deterministic_replay_hash_and_future_mutation_isolation() -> None:
    request = order(side="BUY", order_type="LIMIT", quantity=1.0, limit_price=99.0)
    arrival = BookSnapshot(observation_time=ts(0), bids=((99.0, 0.0),), asks=((101.0, 1.0),))
    trades = [TradePrint(ts(100), "sell", 99.0, 1.0)]
    result_a, fills_a = simulate_limit_order(
        request,
        arrival,
        trades,
        fee_bps=0.0,
        queue_fraction=1.0,
        decision_mid=100.0,
    )
    result_b, fills_b = simulate_limit_order(
        request,
        arrival,
        trades,
        fee_bps=0.0,
        queue_fraction=1.0,
        decision_mid=100.0,
    )
    assert artifact_hash([result_a, *fills_a]) == artifact_hash([result_b, *fills_b])

    future_a = compute_markouts(fills_a, [book(0), book(100)], [100])
    future_b = compute_markouts(
        fills_b,
        [
            book(0),
            BookSnapshot(observation_time=ts(100), bids=((110.0, 1.0),), asks=((112.0, 1.0),)),
        ],
        [100],
    )
    assert artifact_hash([result_a, *fills_a]) == artifact_hash([result_b, *fills_b])
    assert future_a != future_b
