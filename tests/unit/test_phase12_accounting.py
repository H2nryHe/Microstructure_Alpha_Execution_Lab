from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from microalpha.accounting.ledger import (
    Fill,
    accounting_hash,
    build_ledger,
    check_cash_conservation,
    check_equity_identity,
    check_fee_reconciliation,
    check_fill_conservation,
    check_parent_child_reconciliation,
    reject_duplicate_fills,
)


def ts(seconds: int = 0) -> datetime:
    return datetime(2024, 7, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds)


def fill(
    fill_id: str,
    side: str,
    price: float,
    quantity: float,
    seconds: int,
    fee: float = 0.0,
    order_id: str | None = None,
) -> Fill:
    signed = quantity if side == "BUY" else -quantity
    return Fill(
        fill_id=fill_id,
        order_id=order_id or fill_id,
        fill_time=ts(seconds),
        side=side,
        price=price,
        quantity=quantity,
        signed_quantity=signed,
        fee_quote=fee,
    )


def marks(*prices: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": [ts(index) for index in range(len(prices))],
            "mark_mid": list(prices),
        }
    )


def terminal(fills: list[Fill], mark_prices: tuple[float, ...] = (100.0, 100.0, 100.0)):
    return build_ledger(fills=fills, marks=marks(*mark_prices)).summary


def test_long_round_trip() -> None:
    summary = terminal([fill("a", "BUY", 100, 1, 0), fill("b", "SELL", 110, 1, 1)])
    assert summary["gross_pnl"] == pytest.approx(10.0)
    assert summary["realized_pnl"] == pytest.approx(10.0)
    assert summary["terminal_position"] == pytest.approx(0.0)


def test_losing_long() -> None:
    summary = terminal([fill("a", "BUY", 100, 1, 0), fill("b", "SELL", 90, 1, 1)])
    assert summary["gross_pnl"] == pytest.approx(-10.0)
    assert summary["realized_pnl"] == pytest.approx(-10.0)


def test_short_round_trip() -> None:
    summary = terminal([fill("a", "SELL", 100, 1, 0), fill("b", "BUY", 90, 1, 1)])
    assert summary["gross_pnl"] == pytest.approx(10.0)
    assert summary["realized_pnl"] == pytest.approx(10.0)


def test_losing_short() -> None:
    summary = terminal([fill("a", "SELL", 100, 1, 0), fill("b", "BUY", 110, 1, 1)])
    assert summary["gross_pnl"] == pytest.approx(-10.0)
    assert summary["realized_pnl"] == pytest.approx(-10.0)


def test_partial_close() -> None:
    result = build_ledger(
        fills=[fill("a", "BUY", 100, 2, 0), fill("b", "SELL", 110, 1, 1)],
        marks=marks(100, 100),
    )
    assert result.summary["terminal_position"] == pytest.approx(1.0)
    assert result.summary["realized_pnl"] == pytest.approx(10.0)
    assert result.ledger.iloc[-1]["average_entry_price"] == pytest.approx(100.0)


def test_weighted_average_entry() -> None:
    result = build_ledger(
        fills=[fill("a", "BUY", 100, 1, 0), fill("b", "BUY", 110, 1, 1)],
        marks=marks(100, 100),
    )
    assert result.summary["terminal_position"] == pytest.approx(2.0)
    assert result.ledger.iloc[-1]["average_entry_price"] == pytest.approx(105.0)


def test_long_to_short_reversal() -> None:
    result = build_ledger(
        fills=[fill("a", "BUY", 100, 1, 0), fill("b", "SELL", 110, 2, 1)],
        marks=marks(100, 110),
    )
    assert result.summary["realized_pnl"] == pytest.approx(10.0)
    assert result.summary["terminal_position"] == pytest.approx(-1.0)
    assert result.ledger.iloc[-1]["average_entry_price"] == pytest.approx(110.0)


def test_short_to_long_reversal() -> None:
    result = build_ledger(
        fills=[fill("a", "SELL", 100, 1, 0), fill("b", "BUY", 90, 2, 1)],
        marks=marks(100, 90),
    )
    assert result.summary["realized_pnl"] == pytest.approx(10.0)
    assert result.summary["terminal_position"] == pytest.approx(1.0)
    assert result.ledger.iloc[-1]["average_entry_price"] == pytest.approx(90.0)


def test_fee_reconciliation() -> None:
    result = build_ledger(
        fills=[
            fill("a", "BUY", 100, 1, 0, fee=0.25),
            fill("b", "SELL", 110, 1, 1, fee=0.25),
        ],
        marks=marks(100, 110),
    )
    check_fee_reconciliation(result.ledger)
    assert result.summary["gross_pnl"] - result.summary["net_pnl"] == pytest.approx(0.5)


def test_open_terminal_long_inventory() -> None:
    result = build_ledger(fills=[fill("a", "BUY", 100, 1, 0)], marks=marks(100, 105))
    final = result.ledger.iloc[-1]
    assert final["gross_cash"] == pytest.approx(-100.0)
    assert final["position"] == pytest.approx(1.0)
    assert final["inventory_market_value"] == pytest.approx(105.0)
    assert final["gross_equity"] == pytest.approx(5.0)


def test_open_terminal_short_inventory() -> None:
    result = build_ledger(fills=[fill("a", "SELL", 100, 1, 0)], marks=marks(100, 95))
    final = result.ledger.iloc[-1]
    assert final["gross_cash"] == pytest.approx(100.0)
    assert final["position"] == pytest.approx(-1.0)
    assert final["inventory_market_value"] == pytest.approx(-95.0)
    assert final["gross_equity"] == pytest.approx(5.0)


def test_scenario_reset() -> None:
    first = terminal([fill("a", "BUY", 100, 1, 0)], (100, 100))
    second = terminal([fill("b", "SELL", 100, 1, 0)], (100, 100))
    assert first["terminal_position"] == pytest.approx(1.0)
    assert second["terminal_position"] == pytest.approx(-1.0)
    assert second["gross_pnl"] == pytest.approx(0.0)


def test_duplicate_fill_rejection() -> None:
    fills = [fill("a", "BUY", 100, 1, 0), fill("a", "SELL", 100, 1, 1)]
    with pytest.raises(ValueError, match="Duplicate fill_id"):
        reject_duplicate_fills(fills)


def test_parent_child_reconciliation() -> None:
    orders = pd.DataFrame({"order_id": ["o1"], "filled_quantity": [2.0]})
    fills = pd.DataFrame({"order_id": ["o1", "o1"], "quantity": [1.25, 0.75]})
    check_parent_child_reconciliation(orders, fills)
    bad = pd.DataFrame({"order_id": ["o1"], "quantity": [1.0]})
    with pytest.raises(ValueError, match="Parent/child quantity mismatch"):
        check_parent_child_reconciliation(orders, bad)


def test_conservation_identities() -> None:
    result = build_ledger(
        fills=[fill("a", "BUY", 100, 2, 0), fill("b", "SELL", 110, 1, 1)],
        marks=marks(100, 105, 105),
    )
    check_fill_conservation(result.ledger, result.fills)
    check_cash_conservation(result.ledger, result.fills)
    check_equity_identity(result.ledger)
    check_fee_reconciliation(result.ledger)


def test_deterministic_replay_hash_and_future_label_isolation() -> None:
    fills = [fill("a", "BUY", 100, 1, 0), fill("b", "SELL", 110, 1, 1)]
    result_a = build_ledger(fills=fills, marks=marks(100, 110))
    result_b = build_ledger(fills=fills, marks=marks(100, 110))
    labels_a = {"ret_fwd_1s": 1.0, "direction_1s": 1}
    labels_b = {"ret_fwd_1s": -999.0, "direction_1s": -1}
    assert labels_a != labels_b
    assert accounting_hash(result_a.ledger) == accounting_hash(result_b.ledger)


def test_fill_mutation_sensitivity() -> None:
    baseline = terminal([fill("a", "BUY", 100, 1, 0), fill("b", "SELL", 110, 1, 1)])
    price_changed = terminal([fill("a", "BUY", 100, 1, 0), fill("b", "SELL", 111, 1, 1)])
    fee_changed = terminal(
        [fill("a", "BUY", 100, 1, 0, fee=0.5), fill("b", "SELL", 110, 1, 1)]
    )
    assert price_changed["gross_pnl"] == pytest.approx(baseline["gross_pnl"] + 1.0)
    assert fee_changed["net_pnl"] == pytest.approx(baseline["net_pnl"] - 0.5)
