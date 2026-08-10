from __future__ import annotations

import pandas as pd
import pytest

from microalpha.research.phase13 import (
    apply_fee_overlay,
    assert_net_pnl_monotonic_nonincreasing,
    fee_quote,
    interpolate_fee_breakeven,
    market_breakeven_fee_bps,
    passive_breakevens,
    role_turnover_and_fees,
    terminal_inventory_stress_rows,
    validate_latency_causality,
)


def test_market_fee_exact() -> None:
    assert fee_quote(100_000.0, 0.25) == pytest.approx(2.5)


def test_market_breakeven_fee_exact() -> None:
    result = market_breakeven_fee_bps(10.0, 100_000.0)
    assert result["breakeven_fee_status"] == "DEFINED"
    assert result["breakeven_fee_bps"] == pytest.approx(1.0)


def test_net_pnl_monotonicity() -> None:
    rows = [
        {"fee_bps": 0.0, "net_pnl": 10.0},
        {"fee_bps": 0.5, "net_pnl": 5.0},
        {"fee_bps": 1.0, "net_pnl": 0.0},
    ]
    assert_net_pnl_monotonic_nonincreasing(rows)
    with pytest.raises(ValueError, match="Net PnL increased"):
        assert_net_pnl_monotonic_nonincreasing(
            [
                {"fee_bps": 0.0, "net_pnl": 10.0},
                {"fee_bps": 1.0, "net_pnl": 11.0},
            ]
        )


def test_maker_rebate() -> None:
    fills = pd.DataFrame(
        [
            {
                "price": 100.0,
                "signed_quantity": 10.0,
                "liquidity_role": "maker",
            }
        ]
    )
    fees = role_turnover_and_fees(fills, maker_fee_bps=-0.5, taker_fee_bps=1.0)
    assert fees.maker_turnover == pytest.approx(1000.0)
    assert fees.maker_fees == pytest.approx(-0.05)
    overlay = apply_fee_overlay(1.0, fees)
    assert overlay["net_pnl"] == pytest.approx(1.05)


def test_mixed_maker_taker_fee_reconciliation() -> None:
    fills = pd.DataFrame(
        [
            {"price": 100.0, "signed_quantity": 10.0, "liquidity_role": "maker"},
            {
                "price": 200.0,
                "signed_quantity": -5.0,
                "liquidity_role": "taker_marketable_limit",
            },
        ]
    )
    fees = role_turnover_and_fees(fills, maker_fee_bps=-1.0, taker_fee_bps=2.0)
    assert fees.maker_turnover == pytest.approx(1000.0)
    assert fees.taker_turnover == pytest.approx(1000.0)
    assert fees.maker_fees == pytest.approx(-0.1)
    assert fees.taker_fees == pytest.approx(0.2)
    assert fees.total_fees == pytest.approx(0.1)


def test_no_spread_double_count_fee_overlay_does_not_change_gross() -> None:
    fees = role_turnover_and_fees(
        pd.DataFrame(
            [{"price": 100.0, "signed_quantity": 10.0, "liquidity_role": "taker"}]
        ),
        maker_fee_bps=0.0,
        taker_fee_bps=1.0,
        force_all_taker=True,
    )
    overlay = apply_fee_overlay(12.34, fees)
    assert overlay["gross_pnl"] == pytest.approx(12.34)
    assert overlay["gross_pnl_unchanged_by_fee_overlay"] is True
    assert overlay["net_pnl"] == pytest.approx(12.24)


def test_zero_fee_interpolation_reproduces_known_breakeven() -> None:
    rows = pd.DataFrame(
        [
            {"fee_bps": 0.0, "net_pnl": 10.0},
            {"fee_bps": 1.0, "net_pnl": 0.0},
            {"fee_bps": 2.0, "net_pnl": -10.0},
        ]
    )
    assert interpolate_fee_breakeven(rows) == pytest.approx(1.0)


def test_passive_breakeven_rebate_flag() -> None:
    result = passive_breakevens(
        gross_pnl=-1.0,
        maker_turnover=10_000.0,
        taker_turnover=0.0,
        maker_fee_bps=0.0,
        taker_fee_bps=0.0,
    )
    assert result["breakeven_maker_fee_bps_at_fixed_taker"] == pytest.approx(-1.0)
    assert result["maker_breakeven_requires_rebate"] is True


def test_latency_causality() -> None:
    orders = pd.DataFrame(
        [
            {
                "order_create_time": "2024-07-01T00:00:00.000000+00:00",
                "order_arrival_time": "2024-07-01T00:00:00.050000+00:00",
            }
        ]
    )
    fills = pd.DataFrame(
        [
            {
                "fill_time": "2024-07-01T00:00:00.050000+00:00",
                "order_arrival_time": "2024-07-01T00:00:00.050000+00:00",
            }
        ]
    )
    validate_latency_causality(orders, fills, latency_ms=50)
    bad_fills = fills.assign(fill_time="2024-07-01T00:00:00.049999+00:00")
    with pytest.raises(ValueError, match="Fill before order arrival"):
        validate_latency_causality(orders, bad_fills, latency_ms=50)


def test_latency_does_not_mutate_signal_times() -> None:
    signal_times = pd.Series(pd.to_datetime(["2024-07-01T00:00:00Z"], utc=True))
    before = signal_times.copy(deep=True)
    orders = pd.DataFrame(
        [
            {
                "order_create_time": "2024-07-01T00:00:00.000000+00:00",
                "order_arrival_time": "2024-07-01T00:00:00.250000+00:00",
            }
        ]
    )
    validate_latency_causality(orders, pd.DataFrame(), latency_ms=250)
    pd.testing.assert_series_equal(signal_times, before)


def test_terminal_inventory_stress_and_scenario_isolation() -> None:
    scenario_a = {"model": "a", "latency_ms": 0}
    scenario_b = {"model": "b", "latency_ms": 0}
    rows_a = terminal_inventory_stress_rows(
        scenario=scenario_a,
        terminal_position=2.0,
        terminal_mark_mid=100.0,
        base_terminal_equity=5.0,
        shock_bps_values=[-10, 0, 10],
    )
    rows_b = terminal_inventory_stress_rows(
        scenario=scenario_b,
        terminal_position=-2.0,
        terminal_mark_mid=100.0,
        base_terminal_equity=5.0,
        shock_bps_values=[10],
    )
    assert rows_a[0]["terminal_equity_delta"] == pytest.approx(-0.2)
    assert rows_a[-1]["terminal_equity_delta"] == pytest.approx(0.2)
    assert rows_b[0]["terminal_equity_delta"] == pytest.approx(-0.2)
    assert {row["model"] for row in rows_a} == {"a"}
    assert {row["model"] for row in rows_b} == {"b"}
