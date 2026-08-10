"""Phase 13 transaction-cost and latency sensitivity helpers."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable

import pandas as pd

PHASE13_COST_LATENCY_PLAN_HASH = "fadafb1a634f9661d5c664f3a716a8ead8c24e4abf61a221e94668bab9f0a5f1"
PHASE12_COMMIT_SHA = "78f396b47cd52ea40c5ba8b9e7dfb0551aad4302"
PHASE12_ACCOUNTING_ARTIFACT_HASH = (
    "560d48d2656cde46865bc26dd9cd3c853ef6717ed54e2a911ad8a80588c4cc0b"
)
PHASE12_RESULTS_HASH = "c8fb3c53e09ed36c5d41d72370c3bbc624c37ae0a9a910f0da3460784ce8a012"

ROLE_TAKER_MARKET = "taker"
ROLE_TAKER_MARKETABLE_LIMIT = "taker_marketable_limit"
ROLE_MAKER = "maker"


@dataclass(frozen=True)
class FeeBreakdown:
    maker_turnover: float
    taker_turnover: float
    maker_fees: float
    taker_fees: float

    @property
    def total_turnover(self) -> float:
        return self.maker_turnover + self.taker_turnover

    @property
    def total_fees(self) -> float:
        return self.maker_fees + self.taker_fees


def fill_notional(price: float, signed_quantity: float) -> float:
    return abs(float(price) * float(signed_quantity))


def fee_quote(notional: float, fee_bps: float) -> float:
    return float(notional) * float(fee_bps) / 10_000.0


def role_turnover_and_fees(
    fills: pd.DataFrame,
    *,
    maker_fee_bps: float,
    taker_fee_bps: float,
    force_all_taker: bool = False,
) -> FeeBreakdown:
    if fills.empty:
        return FeeBreakdown(0.0, 0.0, 0.0, 0.0)
    frame = fills.copy()
    frame["notional"] = [
        fill_notional(price, qty)
        for price, qty in zip(frame["price"], frame["signed_quantity"], strict=True)
    ]
    if force_all_taker:
        maker_turnover = 0.0
        taker_turnover = float(frame["notional"].sum())
    else:
        role = frame["liquidity_role"].astype(str)
        maker_mask = role == ROLE_MAKER
        taker_mask = role.isin([ROLE_TAKER_MARKET, ROLE_TAKER_MARKETABLE_LIMIT])
        unknown_roles = sorted(set(role[~(maker_mask | taker_mask)]))
        if unknown_roles:
            raise ValueError(f"Unknown liquidity_role values: {unknown_roles}")
        maker_turnover = float(frame.loc[maker_mask, "notional"].sum())
        taker_turnover = float(frame.loc[taker_mask, "notional"].sum())
    return FeeBreakdown(
        maker_turnover=maker_turnover,
        taker_turnover=taker_turnover,
        maker_fees=fee_quote(maker_turnover, maker_fee_bps),
        taker_fees=fee_quote(taker_turnover, taker_fee_bps),
    )


def apply_fee_overlay(gross_pnl: float, fees: FeeBreakdown) -> dict[str, float]:
    total_fees = fees.total_fees
    return {
        "gross_pnl": float(gross_pnl),
        "maker_fees": fees.maker_fees,
        "taker_fees": fees.taker_fees,
        "total_fees": total_fees,
        "net_pnl": float(gross_pnl) - total_fees,
        "gross_pnl_unchanged_by_fee_overlay": True,
    }


def market_breakeven_fee_bps(gross_pnl: float, turnover: float) -> dict[str, float | str | None]:
    if turnover <= 0:
        return {"breakeven_fee_status": "NO_TURNOVER", "breakeven_fee_bps": None}
    if gross_pnl < 0:
        return {"breakeven_fee_status": "ALREADY_NEGATIVE", "breakeven_fee_bps": None}
    return {
        "breakeven_fee_status": "DEFINED",
        "breakeven_fee_bps": float(gross_pnl) / float(turnover) * 10_000.0,
    }


def interpolate_fee_breakeven(fee_rows: pd.DataFrame) -> float | None:
    if fee_rows.empty:
        return None
    rows = fee_rows.sort_values("fee_bps").reset_index(drop=True)
    exact = rows.loc[rows["net_pnl"].abs() <= 1e-10]
    if not exact.empty:
        return float(exact.iloc[0]["fee_bps"])
    for left, right in zip(rows.iloc[:-1].itertuples(), rows.iloc[1:].itertuples(), strict=False):
        if left.net_pnl >= 0 >= right.net_pnl and left.fee_bps != right.fee_bps:
            slope = (right.net_pnl - left.net_pnl) / (right.fee_bps - left.fee_bps)
            if abs(slope) <= 1e-20:
                return None
            return float(left.fee_bps - left.net_pnl / slope)
    return None


def passive_breakevens(
    *,
    gross_pnl: float,
    maker_turnover: float,
    taker_turnover: float,
    maker_fee_bps: float,
    taker_fee_bps: float,
) -> dict[str, float | None | str]:
    taker_fee_rate = taker_fee_bps / 10_000.0
    maker_fee_rate = maker_fee_bps / 10_000.0
    maker_be = None
    taker_be = None
    if maker_turnover > 0:
        maker_be = (gross_pnl - taker_fee_rate * taker_turnover) / maker_turnover * 10_000.0
    if taker_turnover > 0:
        taker_be = (gross_pnl - maker_fee_rate * maker_turnover) / taker_turnover * 10_000.0
    return {
        "breakeven_relationship": (
            "gross_pnl - maker_fee_rate * maker_turnover - "
            "taker_fee_rate * taker_turnover = 0"
        ),
        "breakeven_maker_fee_bps_at_fixed_taker": maker_be,
        "breakeven_taker_fee_bps_at_fixed_maker": taker_be,
        "maker_breakeven_requires_rebate": maker_be is not None and maker_be < 0,
        "taker_breakeven_requires_rebate": taker_be is not None and taker_be < 0,
    }


def assert_net_pnl_monotonic_nonincreasing(rows: Iterable[dict[str, Any]]) -> None:
    previous = math.inf
    for row in sorted(rows, key=lambda item: float(item["fee_bps"])):
        current = float(row["net_pnl"])
        if current > previous + 1e-10:
            raise ValueError("Net PnL increased as fee increased for fixed fills")
        previous = current


def terminal_inventory_stress_rows(
    *,
    scenario: dict[str, Any],
    terminal_position: float,
    terminal_mark_mid: float,
    base_terminal_equity: float,
    shock_bps_values: Iterable[float],
) -> list[dict[str, Any]]:
    rows = []
    for shock_bps in shock_bps_values:
        equity_delta = terminal_position * terminal_mark_mid * float(shock_bps) / 10_000.0
        rows.append(
            {
                **scenario,
                "terminal_mark_shock_bps": float(shock_bps),
                "terminal_equity_delta": float(equity_delta),
                "shocked_terminal_equity": float(base_terminal_equity) + float(equity_delta),
            }
        )
    return rows


def validate_latency_causality(
    orders: pd.DataFrame,
    fills: pd.DataFrame,
    *,
    latency_ms: int,
) -> None:
    if orders.empty:
        return
    create_times = pd.to_datetime(orders["order_create_time"], utc=True)
    arrival_times = pd.to_datetime(orders["order_arrival_time"], utc=True)
    expected = create_times + pd.to_timedelta(int(latency_ms), unit="ms")
    if not (arrival_times >= expected).all():
        raise ValueError("Order arrival before configured latency")
    if not (arrival_times == expected).all():
        raise ValueError("Order arrival latency does not match configured scenario")
    if fills.empty:
        return
    fill_times = pd.to_datetime(fills["fill_time"], utc=True)
    fill_arrivals = pd.to_datetime(fills["order_arrival_time"], utc=True)
    if not (fill_times >= fill_arrivals).all():
        raise ValueError("Fill before order arrival")
