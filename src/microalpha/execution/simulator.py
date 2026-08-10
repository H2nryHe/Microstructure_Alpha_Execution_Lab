"""Deterministic Phase 11 event-driven execution simulator.

The core simulator is deliberately small and explicit. It does not hold a
portfolio account; it converts orders into order/fill states against observable
book snapshots and aggressive trade prints.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from bisect import bisect_right
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Literal

Side = Literal["BUY", "SELL"]
OrderType = Literal["MARKET", "LIMIT"]


PHASE11_EXECUTION_PLAN_HASH = "f5fa9ff916ef084cb1f7aa7d95f22058868ed39745aad14c27a0e2c2ee7d81a4"
PHASE11_EXECUTION_CONFIG_HASH = "7886f78e7552404f88ce446094353133a1590d22dd33ae1f3b647a3eb24132ef"
PHASE10_COMMIT_SHA = "7b4bba3483bd6a7a3ae52acfd12bc91a830f6901"
PHASE10_SIGNAL_ARTIFACT_HASH = "68edd84a5ea6b72035976a0b0f48aabfc0183e17d6946fcbf69da7190f5de5d6"
PHASE10_RESULTS_HASH = "604a7b8a83990b9052c8fd329d93e93759a58d4b98960c2362f104a2d4b14f71"

ORDER_STATES = {
    "CREATED",
    "IN_FLIGHT",
    "RESTING",
    "PARTIALLY_FILLED",
    "FILLED",
    "CANCEL_PENDING",
    "CANCELLED",
    "EXPIRED",
    "REJECTED",
}


def utc_datetime(value: datetime | str) -> datetime:
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    else:
        parsed = value
    if parsed.tzinfo is None:
        raise ValueError(f"Timestamp must be timezone-aware: {value}")
    return parsed.astimezone(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class ExecutionConfig:
    target_order_notional_usd: float = 10_000.0
    market_data_latency_ms: int = 0
    decision_latency_ms: int = 0
    order_latency_ms: int = 0
    cancel_latency_ms: int = 100
    queue_fraction: float = 1.0
    limit_ttl_ms: int = 1000
    fee_bps: float = 0.0


@dataclass(frozen=True)
class BookSnapshot:
    observation_time: datetime
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]

    @property
    def best_bid(self) -> float:
        return self.bids[0][0]

    @property
    def best_ask(self) -> float:
        return self.asks[0][0]

    @property
    def mid(self) -> float:
        return (self.best_bid + self.best_ask) / 2.0

    def quantity_at(self, side: str, price: float) -> float:
        levels = self.bids if side == "bid" else self.asks
        for level_price, quantity in levels:
            if level_price == price:
                return quantity
        return 0.0


@dataclass(frozen=True)
class TradePrint:
    observation_time: datetime
    side: str
    price: float
    quantity: float


@dataclass(frozen=True)
class OrderRequest:
    order_id: str
    date: str
    signal_id: str
    model: str
    side: Side
    order_type: OrderType
    quantity: float
    limit_price: float | None
    order_create_time: datetime
    order_arrival_time: datetime
    status: str = "CREATED"
    filled_quantity: float = 0.0
    remaining_quantity: float = 0.0
    cancel_requested_time: datetime | None = None
    cancel_effective_time: datetime | None = None
    expiration_time: datetime | None = None


@dataclass(frozen=True)
class ChildFill:
    order_id: str
    date: str
    model: str
    side: Side
    order_type: OrderType
    fill_time: datetime
    order_arrival_time: datetime
    price: float
    quantity: float
    signed_quantity: float
    book_level: int
    liquidity_role: str
    fee_rate_bps: float
    fee_quote: float
    decision_mid: float
    arrival_mid: float
    signed_spread_vs_decision_mid: float
    signed_spread_vs_arrival_mid: float
    implementation_shortfall_vs_decision_mid: float


def make_order_id(*parts: object) -> str:
    payload = "|".join(str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def snapshot_asof(snapshots: list[BookSnapshot], timestamp: datetime) -> BookSnapshot:
    if not snapshots:
        raise ValueError("Cannot select from empty snapshot list")
    times = [snapshot.observation_time for snapshot in snapshots]
    index = bisect_right(times, utc_datetime(timestamp)) - 1
    if index < 0:
        raise ValueError(f"No observable book snapshot at or before {timestamp}")
    return snapshots[index]


def first_snapshot_at_or_after(
    snapshots: list[BookSnapshot],
    timestamp: datetime,
) -> BookSnapshot | None:
    target = utc_datetime(timestamp)
    for snapshot in snapshots:
        if snapshot.observation_time >= target:
            return snapshot
    return snapshots[-1] if snapshots else None


def _side_sign(side: Side) -> int:
    return 1 if side == "BUY" else -1


def _shortfall(side: Side, fill_price: float, mid: float) -> float:
    if side == "BUY":
        return (fill_price - mid) / mid
    return (mid - fill_price) / mid


def _spread(side: Side, fill_price: float, mid: float) -> float:
    return _side_sign(side) * (fill_price - mid) / mid


def _fee_quote(price: float, quantity: float, fee_bps: float) -> float:
    return price * quantity * fee_bps / 10_000.0


def _depth_for_side(snapshot: BookSnapshot, side: Side) -> tuple[tuple[float, float], ...]:
    return snapshot.asks if side == "BUY" else snapshot.bids


def _limit_allows(side: Side, price: float, limit_price: float | None) -> bool:
    if limit_price is None:
        return True
    if side == "BUY":
        return price <= limit_price
    return price >= limit_price


def _consume_depth(
    *,
    order: OrderRequest,
    snapshot: BookSnapshot,
    quantity: float,
    limit_price: float | None,
    fee_bps: float,
    decision_mid: float,
    arrival_mid: float,
    liquidity_role: str,
    fill_time: datetime,
) -> tuple[list[ChildFill], float, int]:
    remaining = quantity
    fills: list[ChildFill] = []
    levels_consumed = 0
    depth_levels = _depth_for_side(snapshot, order.side)
    for level_index, (price, available) in enumerate(depth_levels, start=1):
        if remaining <= 1e-12 or not _limit_allows(order.side, price, limit_price):
            break
        fill_quantity = min(remaining, available)
        if fill_quantity <= 0:
            continue
        levels_consumed = level_index
        remaining -= fill_quantity
        fills.append(
            ChildFill(
                order_id=order.order_id,
                date=order.date,
                model=order.model,
                side=order.side,
                order_type=order.order_type,
                fill_time=fill_time,
                order_arrival_time=order.order_arrival_time,
                price=price,
                quantity=fill_quantity,
                signed_quantity=_side_sign(order.side) * fill_quantity,
                book_level=level_index,
                liquidity_role=liquidity_role,
                fee_rate_bps=fee_bps,
                fee_quote=_fee_quote(price, fill_quantity, fee_bps),
                decision_mid=decision_mid,
                arrival_mid=arrival_mid,
                signed_spread_vs_decision_mid=_spread(order.side, price, decision_mid),
                signed_spread_vs_arrival_mid=_spread(order.side, price, arrival_mid),
                implementation_shortfall_vs_decision_mid=_shortfall(
                    order.side, price, decision_mid
                ),
            )
        )
    return fills, max(0.0, remaining), levels_consumed


def execute_market_order(
    order: OrderRequest,
    snapshot: BookSnapshot,
    *,
    fee_bps: float,
    decision_mid: float | None = None,
) -> tuple[dict[str, Any], list[ChildFill]]:
    if order.order_type != "MARKET":
        raise ValueError("execute_market_order requires a MARKET order")
    if snapshot.observation_time > order.order_arrival_time:
        raise ValueError("Market snapshot must be observable at or before order arrival")
    decision = snapshot.mid if decision_mid is None else decision_mid
    fills, residual, levels = _consume_depth(
        order=order,
        snapshot=snapshot,
        quantity=order.quantity,
        limit_price=None,
        fee_bps=fee_bps,
        decision_mid=decision,
        arrival_mid=snapshot.mid,
        liquidity_role="taker",
        fill_time=order.order_arrival_time,
    )
    filled = order.quantity - residual
    status = "FILLED" if residual <= 1e-12 else "PARTIALLY_FILLED" if filled > 0 else "REJECTED"
    return (
        {
            **_order_base(order),
            "status": status,
            "filled_quantity": filled,
            "remaining_quantity": residual,
            "vwap_fill_price": vwap(fills),
            "levels_consumed": levels,
            "insufficient_depth": residual > 1e-12,
        },
        fills,
    )


def simulate_limit_order(
    order: OrderRequest,
    arrival_snapshot: BookSnapshot,
    trades: Iterable[TradePrint],
    *,
    fee_bps: float,
    queue_fraction: float,
    decision_mid: float,
) -> tuple[dict[str, Any], list[ChildFill]]:
    if order.order_type != "LIMIT":
        raise ValueError("simulate_limit_order requires a LIMIT order")
    if order.limit_price is None:
        raise ValueError("Limit order requires limit_price")
    if arrival_snapshot.observation_time > order.order_arrival_time:
        raise ValueError("Limit arrival snapshot must be observable at or before order arrival")

    remaining = order.quantity
    all_fills: list[ChildFill] = []
    arrival_mid = arrival_snapshot.mid
    marketable = (
        (order.side == "BUY" and order.limit_price >= arrival_snapshot.best_ask)
        or (order.side == "SELL" and order.limit_price <= arrival_snapshot.best_bid)
    )
    if marketable:
        taker_fills, residual, _levels = _consume_depth(
            order=order,
            snapshot=arrival_snapshot,
            quantity=remaining,
            limit_price=order.limit_price,
            fee_bps=fee_bps,
            decision_mid=decision_mid,
            arrival_mid=arrival_mid,
            liquidity_role="taker_marketable_limit",
            fill_time=order.order_arrival_time,
        )
        all_fills.extend(taker_fills)
        remaining = residual
        if remaining <= 1e-12:
            return _limit_result(order, "FILLED", all_fills, 0.0, 0.0), all_fills

    book_side = "bid" if order.side == "BUY" else "ask"
    queue_ahead = arrival_snapshot.quantity_at(book_side, order.limit_price) * queue_fraction
    expiration = order.expiration_time or order.order_arrival_time + timedelta(milliseconds=1000)
    cancel_effective = order.cancel_effective_time
    terminal_time = min(expiration, cancel_effective) if cancel_effective else expiration

    for trade in trades:
        trade_time = utc_datetime(trade.observation_time)
        if trade_time <= order.order_arrival_time:
            continue
        if trade_time > terminal_time:
            break
        if not _trade_depletes_passive_order(order, trade):
            continue
        executable = trade.quantity
        if queue_ahead > 1e-12:
            consumed_ahead = min(queue_ahead, executable)
            queue_ahead -= consumed_ahead
            executable -= consumed_ahead
        if executable <= 1e-12 or remaining <= 1e-12:
            continue
        fill_quantity = min(remaining, executable)
        remaining -= fill_quantity
        all_fills.append(
            ChildFill(
                order_id=order.order_id,
                date=order.date,
                model=order.model,
                side=order.side,
                order_type=order.order_type,
                fill_time=trade_time,
                order_arrival_time=order.order_arrival_time,
                price=order.limit_price,
                quantity=fill_quantity,
                signed_quantity=_side_sign(order.side) * fill_quantity,
                book_level=0,
                liquidity_role="maker",
                fee_rate_bps=fee_bps,
                fee_quote=_fee_quote(order.limit_price, fill_quantity, fee_bps),
                decision_mid=decision_mid,
                arrival_mid=arrival_mid,
                signed_spread_vs_decision_mid=_spread(
                    order.side, order.limit_price, decision_mid
                ),
                signed_spread_vs_arrival_mid=_spread(order.side, order.limit_price, arrival_mid),
                implementation_shortfall_vs_decision_mid=_shortfall(
                    order.side, order.limit_price, decision_mid
                ),
            )
        )
        if remaining <= 1e-12:
            break

    if remaining <= 1e-12:
        status = "FILLED"
    elif all_fills:
        status = "PARTIALLY_FILLED"
    elif cancel_effective and cancel_effective <= expiration:
        status = "CANCELLED"
    else:
        status = "EXPIRED"
    return _limit_result(order, status, all_fills, remaining, queue_ahead), all_fills


def _trade_depletes_passive_order(order: OrderRequest, trade: TradePrint) -> bool:
    side = trade.side.lower()
    if order.side == "BUY":
        return side == "sell" and trade.price <= float(order.limit_price)
    return side == "buy" and trade.price >= float(order.limit_price)


def _order_base(order: OrderRequest) -> dict[str, Any]:
    return {
        "order_id": order.order_id,
        "date": order.date,
        "signal_id": order.signal_id,
        "model": order.model,
        "side": order.side,
        "order_type": order.order_type,
        "quantity": order.quantity,
        "limit_price": order.limit_price,
        "order_create_time": iso(order.order_create_time),
        "order_arrival_time": iso(order.order_arrival_time),
        "cancel_requested_time": iso(order.cancel_requested_time)
        if order.cancel_requested_time
        else "",
        "cancel_effective_time": iso(order.cancel_effective_time)
        if order.cancel_effective_time
        else "",
        "expiration_time": iso(order.expiration_time) if order.expiration_time else "",
        "phase11_execution_plan_hash": PHASE11_EXECUTION_PLAN_HASH,
    }


def _limit_result(
    order: OrderRequest,
    status: str,
    fills: list[ChildFill],
    remaining: float,
    queue_ahead: float,
) -> dict[str, Any]:
    return {
        **_order_base(order),
        "status": status,
        "filled_quantity": order.quantity - remaining,
        "remaining_quantity": remaining,
        "vwap_fill_price": vwap(fills),
        "levels_consumed": max((fill.book_level for fill in fills), default=0),
        "insufficient_depth": False,
        "queue_ahead_at_arrival": queue_ahead,
        "fill_fraction": (
            0.0 if order.quantity == 0 else (order.quantity - remaining) / order.quantity
        ),
        "time_to_first_fill_ms": _time_to_first_fill_ms(order, fills),
        "time_to_full_fill_ms": _time_to_full_fill_ms(order, fills, remaining),
    }


def _time_to_first_fill_ms(order: OrderRequest, fills: list[ChildFill]) -> float | None:
    if not fills:
        return None
    return (fills[0].fill_time - order.order_arrival_time).total_seconds() * 1000.0


def _time_to_full_fill_ms(
    order: OrderRequest,
    fills: list[ChildFill],
    remaining: float,
) -> float | None:
    if remaining > 1e-12 or not fills:
        return None
    return (fills[-1].fill_time - order.order_arrival_time).total_seconds() * 1000.0


def vwap(fills: list[ChildFill]) -> float | None:
    quantity = sum(fill.quantity for fill in fills)
    if quantity <= 0:
        return None
    return sum(fill.price * fill.quantity for fill in fills) / quantity


def compute_markouts(
    fills: Iterable[ChildFill],
    snapshots: list[BookSnapshot],
    horizons_ms: Iterable[int],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for fill in fills:
        row = {
            "order_id": fill.order_id,
            "fill_time": iso(fill.fill_time),
            "side": fill.side,
            "fill_price": fill.price,
        }
        sign = _side_sign(fill.side)
        for horizon in horizons_ms:
            target = fill.fill_time + timedelta(milliseconds=int(horizon))
            future = first_snapshot_at_or_after(snapshots, target)
            key = f"signed_markout_{horizon}ms"
            row[key] = (
                None
                if future is None
                else sign * (future.mid - fill.price) / fill.price
            )
        rows.append(row)
    return rows


def validate_no_fill_before_arrival(fills: Iterable[ChildFill]) -> None:
    for fill in fills:
        if fill.fill_time < fill.order_arrival_time:
            raise ValueError(f"Fill before arrival for order {fill.order_id}")


def artifact_hash(records: Iterable[dict[str, Any] | ChildFill]) -> str:
    normalized = []
    for record in records:
        value = asdict(record) if dataclasses.is_dataclass(record) else dict(record)
        normalized.append(_jsonable(value))
    encoded = json.dumps(
        normalized,
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
    return value
