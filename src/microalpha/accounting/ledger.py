"""Self-financing average-cost accounting ledger for Phase 12."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

import numpy as np
import pandas as pd

PHASE12_ACCOUNTING_PLAN_HASH = "a43f49a5d99393cc26b76e86628e67c4459a215f2eb5ad3a241dd339ee3094a9"
PHASE11_COMMIT_SHA = "0a4ef8c2c6d5b98a3709aa0f95400f21a4e8c44e"
PHASE11_EXECUTION_ARTIFACT_HASH = "893c5196be53a00bcd5fb94362b60dece3da28aea2e264fe1f50bf6bbce415c0"
PHASE11_RESULTS_HASH = "a157c0eb1fb27043f19d6072b215645017d9cbc395b35047dd9b072d9d8ec2e0"

EPSILON = 1e-8


def utc_datetime(value: datetime | str | pd.Timestamp) -> datetime:
    if isinstance(value, pd.Timestamp):
        parsed = value.to_pydatetime()
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = value
    if parsed.tzinfo is None:
        raise ValueError(f"Timestamp must be timezone-aware: {value}")
    return parsed.astimezone(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class ScenarioKey:
    date: str
    model: str
    mode: str
    latency_ms: int

    @property
    def relative_id(self) -> str:
        return (
            f"{self.mode}/date={self.date}/model={self.model}/"
            f"latency_ms={self.latency_ms}"
        )


@dataclass(frozen=True)
class Fill:
    fill_id: str
    order_id: str
    fill_time: datetime
    side: str
    price: float
    quantity: float
    signed_quantity: float
    fee_quote: float = 0.0
    child_index: int = 0

    @property
    def notional(self) -> float:
        return abs(self.price * self.signed_quantity)


@dataclass(frozen=True)
class LedgerResult:
    ledger: pd.DataFrame
    fills: pd.DataFrame
    summary: dict[str, Any]


def reject_duplicate_fills(fills: Iterable[Fill]) -> None:
    seen: set[str] = set()
    for fill in fills:
        if fill.fill_id in seen:
            raise ValueError(f"Duplicate fill_id detected: {fill.fill_id}")
        seen.add(fill.fill_id)


def sort_fills(fills: Iterable[Fill]) -> list[Fill]:
    ordered = sorted(
        fills,
        key=lambda fill: (fill.fill_time, fill.order_id, fill.child_index, fill.fill_id),
    )
    reject_duplicate_fills(ordered)
    return ordered


def process_fill(
    *,
    fill: Fill,
    position: float,
    average_entry_price: float,
    realized_pnl: float,
    gross_cash: float,
    net_cash: float,
    fees_paid: float,
    turnover: float,
    buy_notional: float,
    sell_notional: float,
    gross_traded_quantity: float,
) -> dict[str, float]:
    signed = fill.signed_quantity
    if abs(signed) < EPSILON:
        return locals_without_fill(
            position,
            average_entry_price,
            realized_pnl,
            gross_cash,
            net_cash,
            fees_paid,
            turnover,
            buy_notional,
            sell_notional,
            gross_traded_quantity,
        )
    gross_cash += -signed * fill.price
    net_cash += -signed * fill.price - fill.fee_quote
    fees_paid += fill.fee_quote
    turnover += fill.notional
    gross_traded_quantity += abs(signed)
    if signed > 0:
        buy_notional += fill.notional
    else:
        sell_notional += fill.notional

    if abs(position) < EPSILON:
        position = signed
        average_entry_price = fill.price
    elif np.sign(position) == np.sign(signed):
        new_abs_position = abs(position) + abs(signed)
        average_entry_price = (
            average_entry_price * abs(position) + fill.price * abs(signed)
        ) / new_abs_position
        position += signed
    else:
        closed_quantity = min(abs(position), abs(signed))
        if position > 0:
            realized_pnl += (fill.price - average_entry_price) * closed_quantity
        else:
            realized_pnl += (average_entry_price - fill.price) * closed_quantity
        residual = abs(signed) - closed_quantity
        position += signed
        if residual > EPSILON and abs(position) > EPSILON:
            average_entry_price = fill.price
        elif abs(position) <= EPSILON:
            position = 0.0
            average_entry_price = 0.0

    return locals_without_fill(
        position,
        average_entry_price,
        realized_pnl,
        gross_cash,
        net_cash,
        fees_paid,
        turnover,
        buy_notional,
        sell_notional,
        gross_traded_quantity,
    )


def locals_without_fill(
    position: float,
    average_entry_price: float,
    realized_pnl: float,
    gross_cash: float,
    net_cash: float,
    fees_paid: float,
    turnover: float,
    buy_notional: float,
    sell_notional: float,
    gross_traded_quantity: float,
) -> dict[str, float]:
    return {
        "position": position,
        "average_entry_price": average_entry_price,
        "realized_pnl": realized_pnl,
        "gross_cash": gross_cash,
        "net_cash": net_cash,
        "fees_paid": fees_paid,
        "turnover": turnover,
        "buy_notional": buy_notional,
        "sell_notional": sell_notional,
        "gross_traded_quantity": gross_traded_quantity,
    }


def unrealized_pnl(position: float, average_entry_price: float, mark_mid: float) -> float:
    if abs(position) < EPSILON:
        return 0.0
    if position > 0:
        return (mark_mid - average_entry_price) * abs(position)
    return (average_entry_price - mark_mid) * abs(position)


def build_ledger(
    *,
    fills: Iterable[Fill],
    marks: pd.DataFrame,
    scenario: ScenarioKey | None = None,
) -> LedgerResult:
    ordered_fills = sort_fills(fills)
    marks_sorted = marks.copy()
    marks_sorted["timestamp"] = pd.to_datetime(marks_sorted["timestamp"], utc=True)
    marks_sorted["mark_mid"] = pd.to_numeric(marks_sorted["mark_mid"], errors="coerce")
    marks_sorted = marks_sorted.dropna(subset=["mark_mid"]).sort_values("timestamp")
    if marks_sorted.empty:
        raise ValueError("Accounting ledger requires at least one valid mark")

    state = locals_without_fill(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    fill_index = 0
    fill_rows: list[dict[str, Any]] = []
    ledger_rows: list[dict[str, Any]] = []
    for mark in marks_sorted.itertuples(index=False):
        timestamp = utc_datetime(mark.timestamp)
        while fill_index < len(ordered_fills) and ordered_fills[fill_index].fill_time <= timestamp:
            fill = ordered_fills[fill_index]
            state = process_fill(fill=fill, **state)
            fill_rows.append(
                {
                    **asdict(fill),
                    "fill_time": iso(fill.fill_time),
                    **state,
                }
            )
            fill_index += 1
        mark_mid = float(mark.mark_mid)
        inventory_value = state["position"] * mark_mid
        gross_equity = state["gross_cash"] + inventory_value
        net_equity = state["net_cash"] + inventory_value
        unrealized = unrealized_pnl(
            state["position"],
            state["average_entry_price"],
            mark_mid,
        )
        row = {
            "timestamp": iso(timestamp),
            "cash": state["net_cash"],
            "gross_cash": state["gross_cash"],
            "net_cash": state["net_cash"],
            "position": state["position"],
            "average_entry_price": state["average_entry_price"],
            "mark_mid": mark_mid,
            "inventory_market_value": inventory_value,
            "gross_equity": gross_equity,
            "net_equity": net_equity,
            "gross_pnl": gross_equity,
            "net_pnl": net_equity,
            "realized_pnl": state["realized_pnl"],
            "unrealized_pnl": unrealized,
            "fees_paid": state["fees_paid"],
            "turnover": state["turnover"],
            "buy_notional": state["buy_notional"],
            "sell_notional": state["sell_notional"],
            "gross_traded_quantity": state["gross_traded_quantity"],
        }
        if scenario is not None:
            row.update(asdict(scenario))
        ledger_rows.append(row)

    terminal = ledger_rows[-1]
    summary = {
        "terminal_position": float(terminal["position"]),
        "terminal_mark_mid": float(terminal["mark_mid"]),
        "terminal_inventory_value": float(terminal["inventory_market_value"]),
        "gross_pnl": float(terminal["gross_pnl"]),
        "net_pnl": float(terminal["net_pnl"]),
        "realized_pnl": float(terminal["realized_pnl"]),
        "terminal_unrealized_pnl": float(terminal["unrealized_pnl"]),
        "fees_paid": float(terminal["fees_paid"]),
        "turnover": float(terminal["turnover"]),
        "buy_notional": float(terminal["buy_notional"]),
        "sell_notional": float(terminal["sell_notional"]),
        "gross_traded_quantity": float(terminal["gross_traded_quantity"]),
        "fill_count": len(ordered_fills),
        "ledger_rows": len(ledger_rows),
    }
    if scenario is not None:
        summary.update(asdict(scenario))
    return LedgerResult(
        ledger=pd.DataFrame(ledger_rows),
        fills=pd.DataFrame(fill_rows),
        summary=summary,
    )


def check_parent_child_reconciliation(
    orders: pd.DataFrame,
    fills: pd.DataFrame,
    *,
    tolerance: float = 1e-7,
) -> None:
    if not set(fills["order_id"]).issubset(set(orders["order_id"])):
        missing = set(fills["order_id"]) - set(orders["order_id"])
        raise ValueError(f"Child fill references missing parent order: {sorted(missing)[:3]}")
    child = fills.groupby("order_id")["quantity"].sum()
    parent = orders.set_index("order_id")["filled_quantity"]
    for order_id, child_quantity in child.items():
        if abs(float(parent.loc[order_id]) - float(child_quantity)) > tolerance:
            raise ValueError(f"Parent/child quantity mismatch for {order_id}")


def check_fill_conservation(ledger: pd.DataFrame, fills: pd.DataFrame) -> None:
    expected = float(fills["signed_quantity"].sum()) if not fills.empty else 0.0
    observed = float(ledger.iloc[-1]["position"])
    if abs(expected - observed) > 1e-7:
        raise ValueError(f"Fill conservation failed: expected {expected}, observed {observed}")


def check_cash_conservation(ledger: pd.DataFrame, fills: pd.DataFrame) -> None:
    expected_gross = float((-fills["signed_quantity"] * fills["price"]).sum())
    expected_net = expected_gross - float(fills["fee_quote"].sum())
    final = ledger.iloc[-1]
    if abs(expected_gross - float(final["gross_cash"])) > 1e-6:
        raise ValueError("Gross cash conservation failed")
    if abs(expected_net - float(final["net_cash"])) > 1e-6:
        raise ValueError("Net cash conservation failed")


def check_equity_identity(ledger: pd.DataFrame) -> None:
    gross_expected = ledger["gross_cash"] + ledger["position"] * ledger["mark_mid"]
    net_expected = ledger["net_cash"] + ledger["position"] * ledger["mark_mid"]
    if float((gross_expected - ledger["gross_equity"]).abs().max()) > 1e-7:
        raise ValueError("Gross equity identity failed")
    if float((net_expected - ledger["net_equity"]).abs().max()) > 1e-7:
        raise ValueError("Net equity identity failed")


def check_fee_reconciliation(ledger: pd.DataFrame) -> None:
    diff = ledger["gross_pnl"] - ledger["net_pnl"]
    mismatch = (diff - ledger["fees_paid"]).abs().max()
    if float(mismatch) > 1e-7:
        raise ValueError("Gross/net fee reconciliation failed")


def accounting_hash(records: Iterable[dict[str, Any]] | pd.DataFrame) -> str:
    if isinstance(records, pd.DataFrame):
        value = records.to_dict(orient="records")
    else:
        value = list(records)
    encoded = json.dumps(
        _jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return iso(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    return value
