"""Phase 5 microstructure feature engineering.

All trailing windows use the causal convention ``(T-W, T]`` where ``T`` is
``feature_cutoff_time``. Events exactly at ``T`` are included, events exactly at
``T-W`` are excluded, and events after ``T`` are excluded. Tardis features use
observation/local time as the eligibility clock.

Microprice convention:
    ask_px_1 * bid_sz_1 / (bid_sz_1 + ask_sz_1)
    + bid_px_1 * ask_sz_1 / (bid_sz_1 + ask_sz_1)

Positive microprice deviation means bid-side displayed pressure; negative means
ask-side displayed pressure. This is a descriptive feature, not a predictive
claim.

OFI event contribution follows Cont-style BBO transitions:
    e_n =
      1{P_bid,n >= P_bid,n-1} * q_bid,n
    - 1{P_bid,n <= P_bid,n-1} * q_bid,n-1
    - 1{P_ask,n <= P_ask,n-1} * q_ask,n
    + 1{P_ask,n >= P_ask,n-1} * q_ask,n-1

The OFI definition is intentionally explicit and must not be silently changed.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
import time
from bisect import bisect_right
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Optional

from microalpha.features.metadata import (
    FEATURE_VERSION,
    FeatureDefinition,
    base_feature_definitions,
    windowed_definition,
)
from microalpha.research.dataset import dataset_hash, parse_iso_utc

STATE_MISSING = ""


@dataclass(frozen=True)
class FeatureConfig:
    feature_version: str = FEATURE_VERSION
    depth_levels: tuple[int, ...] = (5, 10)
    ofi_windows_ms: tuple[int, ...] = (100, 500, 1000, 5000, 30000)
    trade_windows_ms: tuple[int, ...] = (100, 500, 1000, 5000, 30000)
    realized_vol_windows_ms: tuple[int, ...] = (1000, 5000, 30000)
    momentum_windows_ms: tuple[int, ...] = (100, 500, 1000, 5000)


@dataclass(frozen=True)
class BBOState:
    observation_time: datetime
    source_row_number: int
    best_bid: Decimal
    bid_sz_1: Decimal
    best_ask: Decimal
    ask_sz_1: Decimal


@dataclass(frozen=True)
class OFIEvent:
    observation_time: datetime
    source_row_number: int
    ofi_event: Decimal


@dataclass(frozen=True)
class TradeEvent:
    observation_time: datetime
    event_time: datetime
    source_row_number: int
    price: Decimal
    quantity: Decimal
    side: str

    @property
    def signed_quantity(self) -> Decimal:
        return self.quantity if self.side == "buy" else -self.quantity

    @property
    def notional(self) -> Decimal:
        return self.price * self.quantity


@dataclass
class _TradeWindowTotals:
    buy_volume: Decimal = Decimal("0")
    sell_volume: Decimal = Decimal("0")
    buy_notional: Decimal = Decimal("0")
    sell_notional: Decimal = Decimal("0")
    signed_volume: Decimal = Decimal("0")
    count: int = 0


@dataclass
class FeatureBuildStats:
    total_rows: int
    feature_count: int
    output_hash: str
    processing_time_seconds: float
    feature_version: str
    summary: dict[str, dict[str, str]] = field(default_factory=dict)
    audits: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _decimal(value: str) -> Optional[Decimal]:
    if value in (None, "", "nan", "NaN"):
        return None
    return Decimal(value)


def _format(value: Optional[Decimal]) -> str:
    if value is None:
        return STATE_MISSING
    if value.is_nan():
        return "NaN"
    return format(value, "f")


def _format_float(value: Optional[float]) -> str:
    if value is None:
        return STATE_MISSING
    if math.isnan(value):
        return "NaN"
    return repr(float(value))


def _log_return(current: Decimal, previous: Decimal) -> float:
    return math.log(float(current) / float(previous))


def _in_window(event_time: datetime, cutoff: datetime, window_ms: int) -> bool:
    start = cutoff - timedelta(milliseconds=window_ms)
    return start < event_time <= cutoff


def queue_imbalance(bid_size: Decimal, ask_size: Decimal) -> Decimal:
    denominator = bid_size + ask_size
    if denominator == 0:
        return Decimal("NaN")
    return (bid_size - ask_size) / denominator


def depth_imbalance(bid_depth: Decimal, ask_depth: Decimal) -> Decimal:
    denominator = bid_depth + ask_depth
    if denominator == 0:
        return Decimal("NaN")
    return (bid_depth - ask_depth) / denominator


def microprice(
    bid_price: Decimal,
    bid_size: Decimal,
    ask_price: Decimal,
    ask_size: Decimal,
) -> Decimal:
    denominator = bid_size + ask_size
    if denominator == 0:
        return Decimal("NaN")
    return ask_price * bid_size / denominator + bid_price * ask_size / denominator


def ofi_event(previous: BBOState, current: BBOState) -> Decimal:
    contribution = Decimal("0")
    if current.best_bid >= previous.best_bid:
        contribution += current.bid_sz_1
    if current.best_bid <= previous.best_bid:
        contribution -= previous.bid_sz_1
    if current.best_ask <= previous.best_ask:
        contribution -= current.ask_sz_1
    if current.best_ask >= previous.best_ask:
        contribution += previous.ask_sz_1
    return contribution


def read_state_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def read_trade_events(path: str | Path) -> list[TradeEvent]:
    events: list[TradeEvent] = []
    with Path(path).open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        for fallback_row_number, row in enumerate(reader, start=1):
            source_row = row.get("source_row_number")
            receive_time = row.get("receive_time") or row["event_time"]
            events.append(
                TradeEvent(
                    observation_time=parse_iso_utc(receive_time),
                    event_time=parse_iso_utc(row["event_time"]),
                    source_row_number=(
                        int(source_row) if source_row not in (None, "") else fallback_row_number
                    ),
                    price=Decimal(row["price"]),
                    quantity=Decimal(row["quantity"]),
                    side=row.get("side", "").lower(),
                )
            )
    return events


def bbo_from_state_row(row: dict[str, str]) -> Optional[BBOState]:
    if row.get("is_available") == "false":
        return None
    required = ("best_bid", "bid_sz_1", "best_ask", "ask_sz_1")
    if any(row.get(column) in (None, "") for column in required):
        return None
    return BBOState(
        observation_time=parse_iso_utc(row.get("book_observation_time") or row["observation_time"]),
        source_row_number=int(
            row.get("book_source_row_number") or row.get("final_source_row_number", 0)
        ),
        best_bid=Decimal(row["best_bid"]),
        bid_sz_1=Decimal(row["bid_sz_1"]),
        best_ask=Decimal(row["best_ask"]),
        ask_sz_1=Decimal(row["ask_sz_1"]),
    )


def compute_ofi_events(state_rows: list[dict[str, str]]) -> list[OFIEvent]:
    events: list[OFIEvent] = []
    previous: Optional[BBOState] = None
    for row in state_rows:
        current = bbo_from_state_row(row)
        if current is None:
            continue
        value = Decimal("0") if previous is None else ofi_event(previous, current)
        events.append(
            OFIEvent(
                observation_time=current.observation_time,
                source_row_number=current.source_row_number,
                ofi_event=value,
            )
        )
        previous = current
    return events


def _sum_decimal(values: Iterable[Decimal]) -> Decimal:
    total = Decimal("0")
    for value in values:
        total += value
    return total


def _state_features(row: dict[str, str], config: FeatureConfig) -> dict[str, str]:
    if row.get("is_available") == "false":
        return _missing_state_features(config)
    best_bid = _decimal(row.get("best_bid", ""))
    best_ask = _decimal(row.get("best_ask", ""))
    bid_sz_1 = _decimal(row.get("bid_sz_1", ""))
    ask_sz_1 = _decimal(row.get("ask_sz_1", ""))
    if best_bid is None or best_ask is None or bid_sz_1 is None or ask_sz_1 is None:
        return _missing_state_features(config)
    mid = (best_bid + best_ask) / Decimal("2")
    spread = best_ask - best_bid
    mp = microprice(best_bid, bid_sz_1, best_ask, ask_sz_1)
    result = {
        "mid": _format(mid),
        "spread": _format(spread),
        "relative_spread": _format(None if mid == 0 else spread / mid),
        "spread_bps": _format(None if mid == 0 else Decimal("10000") * spread / mid),
        "qi_1": _format(queue_imbalance(bid_sz_1, ask_sz_1)),
        "microprice": _format(mp),
        "microprice_deviation": _format(None if mid == 0 else (mp - mid) / mid),
        "microprice_deviation_bps": _format(
            None if mid == 0 else Decimal("10000") * (mp - mid) / mid
        ),
    }
    for depth in config.depth_levels:
        bid_depth = _depth(row, "bid", depth)
        ask_depth = _depth(row, "ask", depth)
        result[f"bid_depth_{depth}"] = _format(bid_depth)
        result[f"ask_depth_{depth}"] = _format(ask_depth)
        result[f"di_{depth}"] = _format(depth_imbalance(bid_depth, ask_depth))
    return result


def _missing_state_features(config: FeatureConfig) -> dict[str, str]:
    fields = [
        "mid",
        "spread",
        "relative_spread",
        "spread_bps",
        "qi_1",
        "microprice",
        "microprice_deviation",
        "microprice_deviation_bps",
    ]
    for depth in config.depth_levels:
        fields.extend([f"bid_depth_{depth}", f"ask_depth_{depth}", f"di_{depth}"])
    return {field: STATE_MISSING for field in fields}


def _depth(row: dict[str, str], side: str, depth: int) -> Decimal:
    total = Decimal("0")
    for index in range(1, depth + 1):
        value = _decimal(row.get(f"{side}_sz_{index}", ""))
        if value is not None:
            total += value
    return total


def _feature_fieldnames(config: FeatureConfig) -> list[str]:
    base = [
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
    ]
    state = list(_missing_state_features(config).keys())
    ofi = [f"ofi_{_window_name(window)}" for window in config.ofi_windows_ms]
    trade = []
    for window in config.trade_windows_ms:
        suffix = _window_name(window)
        trade.extend(
            [
                f"buy_volume_{suffix}",
                f"sell_volume_{suffix}",
                f"total_volume_{suffix}",
                f"buy_notional_{suffix}",
                f"sell_notional_{suffix}",
                f"trade_count_{suffix}",
                f"trade_volume_{suffix}",
                f"trade_notional_{suffix}",
                f"signed_trade_volume_{suffix}",
                f"trade_imbalance_{suffix}",
            ]
        )
    activity = [f"book_update_count_{_window_name(window)}" for window in config.ofi_windows_ms]
    vol = [f"realized_vol_{_window_name(window)}" for window in config.realized_vol_windows_ms]
    mom = [f"mom_{_window_name(window)}" for window in config.momentum_windows_ms]
    return base + state + ofi + trade + activity + vol + mom


def _window_name(window_ms: int) -> str:
    if window_ms % 1000 == 0:
        return f"{window_ms // 1000}s"
    return f"{window_ms}ms"


def _advance_window(queue, cutoff: datetime, window_ms: int) -> None:
    start = cutoff - timedelta(milliseconds=window_ms)
    while queue and queue[0][0] <= start:
        queue.popleft()


class _OFIWindowAccumulator:
    def __init__(self, windows_ms: Iterable[int]) -> None:
        self._queues = {window: deque() for window in windows_ms}
        self._sums = {window: Decimal("0") for window in windows_ms}

    def append(self, event: OFIEvent) -> None:
        for window, queue in self._queues.items():
            queue.append((event.observation_time, event.ofi_event))
            self._sums[window] += event.ofi_event

    def features(self, cutoff: datetime) -> dict[str, str]:
        result = {}
        for window, queue in self._queues.items():
            start = cutoff - timedelta(milliseconds=window)
            while queue and queue[0][0] <= start:
                _timestamp, value = queue.popleft()
                self._sums[window] -= value
            suffix = _window_name(window)
            result[f"ofi_{suffix}"] = _format(self._sums[window])
            result[f"book_update_count_{suffix}"] = str(len(queue))
        return result


class _TradeWindowAccumulator:
    def __init__(self, windows_ms: Iterable[int]) -> None:
        self._queues = {window: deque() for window in windows_ms}
        self._totals = {window: _TradeWindowTotals() for window in windows_ms}

    def append(self, trade: TradeEvent) -> None:
        for window, queue in self._queues.items():
            queue.append(trade)
            self._add(window, trade)

    def features(self, cutoff: datetime) -> dict[str, str]:
        result = {}
        for window, queue in self._queues.items():
            start = cutoff - timedelta(milliseconds=window)
            while queue and queue[0].observation_time <= start:
                self._subtract(window, queue.popleft())
            suffix = _window_name(window)
            result.update(self._format_window(window, suffix))
        return result

    def _add(self, window: int, trade: TradeEvent) -> None:
        totals = self._totals[window]
        if trade.side == "buy":
            totals.buy_volume += trade.quantity
            totals.buy_notional += trade.notional
        elif trade.side == "sell":
            totals.sell_volume += trade.quantity
            totals.sell_notional += trade.notional
        totals.signed_volume += trade.signed_quantity
        totals.count += 1

    def _subtract(self, window: int, trade: TradeEvent) -> None:
        totals = self._totals[window]
        if trade.side == "buy":
            totals.buy_volume -= trade.quantity
            totals.buy_notional -= trade.notional
        elif trade.side == "sell":
            totals.sell_volume -= trade.quantity
            totals.sell_notional -= trade.notional
        totals.signed_volume -= trade.signed_quantity
        totals.count -= 1

    def _format_window(self, window: int, suffix: str) -> dict[str, str]:
        totals = self._totals[window]
        total_volume = totals.buy_volume + totals.sell_volume
        total_notional = totals.buy_notional + totals.sell_notional
        return {
            f"buy_volume_{suffix}": _format(totals.buy_volume),
            f"sell_volume_{suffix}": _format(totals.sell_volume),
            f"total_volume_{suffix}": _format(total_volume),
            f"trade_volume_{suffix}": _format(total_volume),
            f"buy_notional_{suffix}": _format(totals.buy_notional),
            f"sell_notional_{suffix}": _format(totals.sell_notional),
            f"trade_notional_{suffix}": _format(total_notional),
            f"trade_count_{suffix}": str(totals.count),
            f"signed_trade_volume_{suffix}": _format(totals.signed_volume),
            f"trade_imbalance_{suffix}": _format(
                Decimal("NaN")
                if total_volume == 0
                else (totals.buy_volume - totals.sell_volume) / total_volume
            ),
        }


def build_feature_table(
    *,
    fixed_clock_path: str | Path,
    event_state_path: str | Path,
    trades_path: str | Path,
    output_path: str | Path,
    config: FeatureConfig,
) -> FeatureBuildStats:
    start_time = time.perf_counter()
    fixed_rows = read_state_rows(fixed_clock_path)
    state_rows = read_state_rows(event_state_path)
    trades = read_trade_events(trades_path)
    ofi_events = compute_ofi_events(state_rows)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    ofi_index = 0
    trade_index = 0
    ofi_accumulator = _OFIWindowAccumulator(config.ofi_windows_ms)
    trade_accumulator = _TradeWindowAccumulator(config.trade_windows_ms)
    mid_series = _build_mid_series(state_rows)
    fieldnames = _feature_fieldnames(config)

    with output.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in fixed_rows:
            cutoff = parse_iso_utc(row["feature_cutoff_time"])
            while ofi_index < len(ofi_events) and ofi_events[ofi_index].observation_time <= cutoff:
                ofi_accumulator.append(ofi_events[ofi_index])
                ofi_index += 1
            while trade_index < len(trades) and trades[trade_index].observation_time <= cutoff:
                trade_accumulator.append(trades[trade_index])
                trade_index += 1
            feature_row = {
                "feature_version": config.feature_version,
                "instrument": row["instrument"],
                "observation_time": row["observation_time"],
                "feature_cutoff_time": row["feature_cutoff_time"],
                "is_available": row.get("is_available", ""),
                "book_observation_time": row.get("book_observation_time", ""),
                "book_event_time": row.get("book_event_time", ""),
                "book_source_row_number": row.get("book_source_row_number", ""),
                "latest_trade_observation_time": row.get("latest_trade_observation_time", ""),
                "latest_trade_event_time": row.get("latest_trade_event_time", ""),
            }
            feature_row.update(_state_features(row, config))
            feature_row.update(ofi_accumulator.features(cutoff))
            feature_row.update(trade_accumulator.features(cutoff))
            feature_row.update(_mid_series_features(mid_series, cutoff, config))
            writer.writerow(feature_row)

    output_hash = dataset_hash(output)
    summary = summarize_feature_file(output)
    return FeatureBuildStats(
        total_rows=len(fixed_rows),
        feature_count=len(fieldnames),
        output_hash=output_hash,
        processing_time_seconds=time.perf_counter() - start_time,
        feature_version=config.feature_version,
        summary=summary,
    )


def _ofi_window_features(ofi_windows: dict[int, deque], cutoff: datetime) -> dict[str, str]:
    result = {}
    for window, queue in ofi_windows.items():
        _advance_window(queue, cutoff, window)
        result[f"ofi_{_window_name(window)}"] = _format(_sum_decimal(value for _, value in queue))
        result[f"book_update_count_{_window_name(window)}"] = str(len(queue))
    return result


def _trade_window_features(trade_windows: dict[int, deque], cutoff: datetime) -> dict[str, str]:
    result = {}
    for window, queue in trade_windows.items():
        _advance_window(queue, cutoff, window)
        buy_volume = Decimal("0")
        sell_volume = Decimal("0")
        buy_notional = Decimal("0")
        sell_notional = Decimal("0")
        signed_volume = Decimal("0")
        for _, trade in queue:
            if trade.side == "buy":
                buy_volume += trade.quantity
                buy_notional += trade.notional
            elif trade.side == "sell":
                sell_volume += trade.quantity
                sell_notional += trade.notional
            signed_volume += trade.signed_quantity
        total_volume = buy_volume + sell_volume
        total_notional = buy_notional + sell_notional
        suffix = _window_name(window)
        result[f"buy_volume_{suffix}"] = _format(buy_volume)
        result[f"sell_volume_{suffix}"] = _format(sell_volume)
        result[f"total_volume_{suffix}"] = _format(total_volume)
        result[f"trade_volume_{suffix}"] = _format(total_volume)
        result[f"buy_notional_{suffix}"] = _format(buy_notional)
        result[f"sell_notional_{suffix}"] = _format(sell_notional)
        result[f"trade_notional_{suffix}"] = _format(total_notional)
        result[f"trade_count_{suffix}"] = str(len(queue))
        result[f"signed_trade_volume_{suffix}"] = _format(signed_volume)
        result[f"trade_imbalance_{suffix}"] = _format(
            Decimal("NaN") if total_volume == 0 else (buy_volume - sell_volume) / total_volume
        )
    return result


def _build_mid_series(state_rows: list[dict[str, str]]) -> dict[str, list]:
    times = []
    mids = []
    return_times = []
    prefix_squared_returns = [0.0]
    previous_mid = None
    for row in state_rows:
        mid = _decimal(row.get("mid", ""))
        if mid is None:
            continue
        timestamp = parse_iso_utc(row["observation_time"])
        times.append(timestamp)
        mids.append(mid)
        if previous_mid is not None and previous_mid > 0 and mid > 0:
            value = _log_return(mid, previous_mid)
            return_times.append(timestamp)
            prefix_squared_returns.append(prefix_squared_returns[-1] + value * value)
        previous_mid = mid
    return {
        "times": times,
        "mids": mids,
        "return_times": return_times,
        "prefix_squared_returns": prefix_squared_returns,
    }


def _mid_series_features(
    mid_series: dict[str, list],
    cutoff: datetime,
    config: FeatureConfig,
) -> dict[str, str]:
    result: dict[str, str] = {}
    times = mid_series["times"]
    mids = mid_series["mids"]
    return_times = mid_series["return_times"]
    prefix = mid_series["prefix_squared_returns"]
    for window in config.realized_vol_windows_ms:
        start = cutoff - timedelta(milliseconds=window)
        left = bisect_right(return_times, start)
        right = bisect_right(return_times, cutoff)
        squared_sum = prefix[right] - prefix[left]
        result[f"realized_vol_{_window_name(window)}"] = _format_float(
            math.sqrt(squared_sum) if right > left else None
        )
    for window in config.momentum_windows_ms:
        target = cutoff - timedelta(milliseconds=window)
        prior = _asof_mid(times, mids, target)
        current = _asof_mid(times, mids, cutoff)
        result[f"mom_{_window_name(window)}"] = _format_float(
            _log_return(current, prior)
            if prior is not None and current is not None and prior > 0 and current > 0
            else None
        )
    return result


def _asof_mid(times: list[datetime], mids: list[Decimal], cutoff: datetime) -> Optional[Decimal]:
    index = bisect_right(times, cutoff) - 1
    if index < 0:
        return None
    return mids[index]


def summarize_feature_file(path: str | Path) -> dict[str, dict[str, str]]:
    important = [
        "qi_1",
        "di_5",
        "di_10",
        "spread_bps",
        "microprice_deviation_bps",
        "ofi_100ms",
        "ofi_1s",
        "ofi_5s",
        "trade_imbalance_1s",
        "trade_count_1s",
        "trade_volume_1s",
        "realized_vol_5s",
        "mom_1s",
    ]
    values_by_feature: dict[str, list[float]] = {feature: [] for feature in important}
    missing_by_feature: dict[str, int] = {feature: 0 for feature in important}
    total = 0
    with Path(path).open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            total += 1
            for feature in important:
                value = row.get(feature, "")
                if value in ("", "NaN"):
                    missing_by_feature[feature] += 1
                    continue
                values_by_feature[feature].append(float(value))
    if total == 0:
        return {}
    return {
        feature: _summarize_values(
            values_by_feature[feature],
            missing_by_feature[feature],
            total,
        )
        for feature in important
    }


def _summarize_values(values: list[float], missing: int, total: int) -> dict[str, str]:
    if not values:
        return {"missing_rate": "1", "count": "0"}
    sorted_values = sorted(values)
    return {
        "missing_rate": repr(missing / total),
        "count": str(len(values)),
        "min": repr(sorted_values[0]),
        "p1": repr(_percentile(sorted_values, 0.01)),
        "p5": repr(_percentile(sorted_values, 0.05)),
        "median": repr(statistics.median(sorted_values)),
        "p95": repr(_percentile(sorted_values, 0.95)),
        "p99": repr(_percentile(sorted_values, 0.99)),
        "max": repr(sorted_values[-1]),
        "mean": repr(statistics.fmean(sorted_values)),
        "std": repr(statistics.pstdev(sorted_values) if len(sorted_values) > 1 else 0.0),
    }


def _percentile(sorted_values: list[float], percentile: float) -> float:
    if not sorted_values:
        return float("nan")
    index = percentile * (len(sorted_values) - 1)
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return sorted_values[lower]
    weight = index - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def feature_metadata(config: FeatureConfig) -> list[FeatureDefinition]:
    definitions = base_feature_definitions()
    definitions.extend([
        FeatureDefinition(
            "spread",
            "best_ask - best_bid",
            "book_state",
            "state",
            "feature_cutoff_time",
            "missing when book state unavailable",
            "price",
        ),
        FeatureDefinition(
            "spread_bps",
            "10000 * (best_ask - best_bid) / mid",
            "book_state",
            "state",
            "feature_cutoff_time",
            "missing when mid is unavailable or zero",
            "basis points",
        ),
        FeatureDefinition(
            "microprice_deviation",
            "(microprice - mid) / mid",
            "book_state",
            "state",
            "feature_cutoff_time",
            "NaN when denominator is zero; missing when state unavailable",
            "ratio",
        ),
        FeatureDefinition(
            "microprice_deviation_bps",
            "10000 * (microprice - mid) / mid",
            "book_state",
            "state",
            "feature_cutoff_time",
            "NaN when denominator is zero; missing when state unavailable",
            "basis points",
        ),
    ])
    for depth in config.depth_levels:
        definitions.extend([
            FeatureDefinition(
                f"bid_depth_{depth}",
                f"sum(bid_sz_1 ... bid_sz_{depth})",
                "book_state",
                "state",
                "feature_cutoff_time",
                "missing levels omitted; missing state leaves feature missing",
                "base asset quantity",
            ),
            FeatureDefinition(
                f"ask_depth_{depth}",
                f"sum(ask_sz_1 ... ask_sz_{depth})",
                "book_state",
                "state",
                "feature_cutoff_time",
                "missing levels omitted; missing state leaves feature missing",
                "base asset quantity",
            ),
            FeatureDefinition(
                f"di_{depth}",
                f"(bid_depth_{depth} - ask_depth_{depth}) / "
                f"(bid_depth_{depth} + ask_depth_{depth})",
                "book_state",
                "state",
                "feature_cutoff_time",
                "NaN when denominator is zero; missing when state unavailable",
                "ratio",
            ),
        ])
    for window in config.ofi_windows_ms:
        suffix = _window_name(window)
        definitions.append(windowed_definition(
            f"ofi_{suffix}",
            "sum(ofi_event) over completed BBO transitions",
            "book_state_events",
            window,
            "zero when no completed BBO transitions are in the window",
            "base asset quantity",
        ))
        definitions.append(windowed_definition(
            f"book_update_count_{suffix}",
            "count(completed BBO transitions)",
            "book_state_events",
            window,
            "zero when no completed BBO transitions are in the window",
            "count",
        ))
    for window in config.trade_windows_ms:
        suffix = _window_name(window)
        definitions.extend([
            windowed_definition(
                f"buy_volume_{suffix}",
                "sum(quantity where aggressive side is buy)",
                "trades",
                window,
                "zero when no buy trades are in the window",
                "base asset quantity",
            ),
            windowed_definition(
                f"sell_volume_{suffix}",
                "sum(quantity where aggressive side is sell)",
                "trades",
                window,
                "zero when no sell trades are in the window",
                "base asset quantity",
            ),
            windowed_definition(
                f"trade_count_{suffix}",
                "count(trades)",
                "trades",
                window,
                "zero when no trades are in the window",
                "count",
            ),
            windowed_definition(
                f"trade_notional_{suffix}",
                "sum(price * quantity)",
                "trades",
                window,
                "zero when no trades are in the window",
                "quote currency notional",
            ),
            windowed_definition(
                f"trade_imbalance_{suffix}",
                "(buy_volume - sell_volume) / (buy_volume + sell_volume)",
                "trades",
                window,
                "NaN when no trades are in the window",
                "ratio",
            ),
        ])
    for window in config.realized_vol_windows_ms:
        suffix = _window_name(window)
        definitions.append(windowed_definition(
            f"realized_vol_{suffix}",
            "sqrt(sum(log(mid_n / mid_{n-1})^2)) over completed state mids",
            "book_state_events",
            window,
            "missing when no trailing mid returns are available",
            "log-return volatility",
        ))
    for window in config.momentum_windows_ms:
        suffix = _window_name(window)
        definitions.append(windowed_definition(
            f"mom_{suffix}",
            "log(mid_asof_T / mid_asof_(T-W))",
            "book_state_events",
            window,
            "missing when either as-of mid is unavailable",
            "log return",
        ))
    return definitions


def metadata_hash(metadata: list[FeatureDefinition]) -> str:
    payload = json.dumps([item.to_dict() for item in metadata], sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
