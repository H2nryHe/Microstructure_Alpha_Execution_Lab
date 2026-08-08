"""Phase 4 causal research dataset construction.

Time contract:
- event_time: exchange-origin timestamp retained for analysis.
- observation_time: local/receive timestamp when the event became observable.
- source_row_number: immutable source-order tie breaker.
- feature_cutoff_time: latest observation time a research row may use.

For Tardis data, causality is based on observation_time/local_timestamp plus
source_row_number. The pipeline never reorders captured data by exchange
event_time.
"""

from __future__ import annotations

import csv
import hashlib
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

from microalpha.book.replay import BookEvent
from microalpha.book.state import BookStateError, OrderBook


@dataclass(frozen=True)
class ResearchConfig:
    depth: int = 10
    sampling_interval_ms: int = 100
    max_staleness_ms: int = 1000


@dataclass
class ResearchBuildStats:
    source_l2_rows: int = 0
    source_trade_rows: int = 0
    event_state_rows: int = 0
    fixed_clock_rows: int = 0
    unavailable_stale_rows: int = 0
    duplicate_research_timestamps: int = 0
    crossed_or_invalid_states: int = 0
    first_observation_time: Optional[str] = None
    last_observation_time: Optional[str] = None
    processing_time_seconds: float = 0.0
    output_hash: str = ""
    row_audits: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def parse_iso_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError(f"Timestamp must be timezone-aware: {value}")
    return parsed.astimezone(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def dataset_hash(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _group_id(event: BookEvent) -> str:
    return event.receive_time or event.event_time


def _state_row(
    *,
    instrument: str,
    event: BookEvent,
    book: OrderBook,
    depth: int,
) -> dict[str, str]:
    snapshot = book.snapshot(depth=depth)
    row = {
        "instrument": instrument,
        "observation_time": event.receive_time or event.event_time,
        "feature_cutoff_time": event.receive_time or event.event_time,
        "state_observation_time": event.receive_time or event.event_time,
        "state_exchange_time": event.event_time,
        "book_event_time": event.event_time,
        "book_observation_time": event.receive_time or event.event_time,
        "book_source_row_number": str(event.source_row_number),
        "final_source_row_number": str(event.source_row_number),
    }
    row.update(snapshot)
    return row


def event_state_fieldnames(depth: int) -> list[str]:
    fields = [
        "instrument",
        "observation_time",
        "feature_cutoff_time",
        "state_observation_time",
        "state_exchange_time",
        "book_event_time",
        "book_observation_time",
        "book_source_row_number",
        "final_source_row_number",
        "best_bid",
        "bid_size",
        "best_ask",
        "ask_size",
        "mid",
        "spread",
        "bid_depth",
        "ask_depth",
    ]
    for index in range(1, depth + 1):
        fields.extend([f"bid_px_{index}", f"bid_sz_{index}"])
    for index in range(1, depth + 1):
        fields.extend([f"ask_px_{index}", f"ask_sz_{index}"])
    return fields


def build_event_state_table(
    *,
    book_events: Iterable[BookEvent],
    output_path: str | Path,
    instrument: str,
    config: ResearchConfig,
) -> ResearchBuildStats:
    """Emit one row per completed logical book-state update."""

    start = time.perf_counter()
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    stats = ResearchBuildStats()
    book = OrderBook()
    initialized = False
    current_group: list[BookEvent] = []
    current_group_id: Optional[str] = None
    seen_observation_times: set[str] = set()
    fieldnames = event_state_fieldnames(config.depth)

    with output.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()

        def flush_group() -> None:
            nonlocal initialized
            if not current_group:
                return
            group_is_snapshot = any(event.update_type == "snapshot" for event in current_group)
            if group_is_snapshot:
                book.clear()
                for event in current_group:
                    book.apply_level(event.side, event.price, event.quantity)
                initialized = True
            elif not initialized:
                return
            else:
                for event in current_group:
                    book.apply_level(event.side, event.price, event.quantity)
            try:
                book.metrics(depth=config.depth)
            except BookStateError:
                stats.crossed_or_invalid_states += 1
                raise
            final_event = current_group[-1]
            row = _state_row(
                instrument=instrument,
                event=final_event,
                book=book,
                depth=config.depth,
            )
            if row["observation_time"] in seen_observation_times:
                stats.duplicate_research_timestamps += 1
            seen_observation_times.add(row["observation_time"])
            if stats.first_observation_time is None:
                stats.first_observation_time = row["observation_time"]
            stats.last_observation_time = row["observation_time"]
            writer.writerow(row)
            stats.event_state_rows += 1

        previous_source_row: Optional[int] = None
        for event in book_events:
            stats.source_l2_rows += 1
            if previous_source_row is not None and event.source_row_number <= previous_source_row:
                raise BookStateError("Book events must preserve source row order")
            previous_source_row = event.source_row_number
            group_id = _group_id(event)
            if current_group_id is None:
                current_group_id = group_id
            if group_id != current_group_id:
                flush_group()
                current_group = []
                current_group_id = group_id
            current_group.append(event)
        flush_group()

    stats.processing_time_seconds = time.perf_counter() - start
    stats.output_hash = dataset_hash(output)
    return stats


@dataclass(frozen=True)
class TradeRecord:
    source_row_number: int
    event_time: str
    observation_time: str
    price: str
    quantity: str
    side: str
    trade_id: str


def read_trades(path: str | Path) -> list[TradeRecord]:
    trades: list[TradeRecord] = []
    with Path(path).open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        for fallback_row_number, row in enumerate(reader, start=1):
            source_row = row.get("source_row_number")
            receive_time = row.get("receive_time") or row["event_time"]
            trades.append(
                TradeRecord(
                    source_row_number=(
                        int(source_row) if source_row not in (None, "") else fallback_row_number
                    ),
                    event_time=row["event_time"],
                    observation_time=receive_time,
                    price=row["price"],
                    quantity=row["quantity"],
                    side=row.get("side", ""),
                    trade_id=row.get("trade_id", ""),
                )
            )
    return trades


def _load_state_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def fixed_clock_fieldnames(depth: int) -> list[str]:
    fields = [
        "instrument",
        "observation_time",
        "feature_cutoff_time",
        "is_available",
        "unavailable_reason",
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
        "latest_trade_source_row_number",
        "latest_trade_price",
        "latest_trade_quantity",
        "latest_trade_side",
    ]
    for index in range(1, depth + 1):
        fields.extend([f"bid_px_{index}", f"bid_sz_{index}"])
    for index in range(1, depth + 1):
        fields.extend([f"ask_px_{index}", f"ask_sz_{index}"])
    return fields


def build_fixed_clock_table(
    *,
    event_state_path: str | Path,
    trades_path: str | Path,
    output_path: str | Path,
    config: ResearchConfig,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
) -> ResearchBuildStats:
    """Build fixed-clock research rows using backward/as-of semantics only."""

    start = time.perf_counter()
    states = _load_state_rows(event_state_path)
    trades = read_trades(trades_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    stats = ResearchBuildStats(
        source_l2_rows=len(states),
        source_trade_rows=len(trades),
    )
    if not states:
        raise ValueError("Cannot build fixed-clock table without state rows")

    state_times = [parse_iso_utc(row["observation_time"]) for row in states]
    trade_times = [parse_iso_utc(trade.observation_time) for trade in trades]
    grid_start = parse_iso_utc(start_time) if start_time else state_times[0]
    grid_end = parse_iso_utc(end_time) if end_time else state_times[-1]
    interval = timedelta(milliseconds=config.sampling_interval_ms)
    max_staleness = timedelta(milliseconds=config.max_staleness_ms)
    state_index = -1
    trade_index = -1
    seen_cutoffs: set[str] = set()
    fieldnames = fixed_clock_fieldnames(config.depth)

    with output.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        cutoff = grid_start
        while cutoff <= grid_end:
            while state_index + 1 < len(states) and state_times[state_index + 1] <= cutoff:
                state_index += 1
            while trade_index + 1 < len(trades) and trade_times[trade_index + 1] <= cutoff:
                trade_index += 1

            cutoff_text = iso(cutoff)
            if cutoff_text in seen_cutoffs:
                stats.duplicate_research_timestamps += 1
            seen_cutoffs.add(cutoff_text)

            row = {
                "instrument": states[0]["instrument"],
                "observation_time": cutoff_text,
                "feature_cutoff_time": cutoff_text,
                "is_available": "false",
                "unavailable_reason": "",
            }
            if state_index < 0:
                row["unavailable_reason"] = "no_prior_book_state"
                stats.unavailable_stale_rows += 1
            else:
                state = states[state_index]
                state_time = state_times[state_index]
                if cutoff - state_time > max_staleness:
                    row["unavailable_reason"] = "stale_book_state"
                    stats.unavailable_stale_rows += 1
                else:
                    row.update(_fixed_row_from_state(state))
                    row["is_available"] = "true"
                    if trade_index >= 0:
                        trade = trades[trade_index]
                        row.update(
                            {
                                "latest_trade_event_time": trade.event_time,
                                "latest_trade_observation_time": trade.observation_time,
                                "latest_trade_source_row_number": str(trade.source_row_number),
                                "latest_trade_price": trade.price,
                                "latest_trade_quantity": trade.quantity,
                                "latest_trade_side": trade.side,
                            }
                        )
            writer.writerow(row)
            stats.fixed_clock_rows += 1
            if stats.first_observation_time is None:
                stats.first_observation_time = cutoff_text
            stats.last_observation_time = cutoff_text
            cutoff += interval

    stats.processing_time_seconds = time.perf_counter() - start
    stats.output_hash = dataset_hash(output)
    return stats


def _fixed_row_from_state(state: dict[str, str]) -> dict[str, str]:
    row = {
        "book_event_time": state["book_event_time"],
        "book_observation_time": state["book_observation_time"],
        "book_source_row_number": state["book_source_row_number"],
        "best_bid": state["best_bid"],
        "bid_sz_1": state.get("bid_sz_1", state.get("bid_size", "")),
        "best_ask": state["best_ask"],
        "ask_sz_1": state.get("ask_sz_1", state.get("ask_size", "")),
        "mid": state["mid"],
        "spread": state["spread"],
    }
    for key, value in state.items():
        if key.startswith(("bid_px_", "bid_sz_", "ask_px_", "ask_sz_")):
            row[key] = value
    return row


def audit_fixed_clock_rows(
    *,
    fixed_clock_path: str | Path,
    event_state_path: str | Path,
    trades_path: str | Path,
    sample_count: int = 5,
) -> list[dict[str, str]]:
    """Return deterministic row audits proving no future source record was selected."""

    fixed_rows = _load_state_rows(fixed_clock_path)
    states = _load_state_rows(event_state_path)
    trades = read_trades(trades_path)
    if not fixed_rows:
        return []
    state_times = [parse_iso_utc(row["observation_time"]) for row in states]
    trade_times = [parse_iso_utc(trade.observation_time) for trade in trades]
    indexes = _deterministic_sample_indexes(len(fixed_rows), sample_count)
    audits = []
    for index in indexes:
        row = fixed_rows[index]
        cutoff = parse_iso_utc(row["feature_cutoff_time"])
        next_state_time = ""
        next_trade_time = ""
        for state_index, state_time in enumerate(state_times):
            if state_time > cutoff:
                next_state_time = states[state_index]["observation_time"]
                break
        for trade_index, trade_time in enumerate(trade_times):
            if trade_time > cutoff:
                next_trade_time = trades[trade_index].observation_time
                break
        audits.append(
            {
                "cutoff_time": row["feature_cutoff_time"],
                "selected_book_state_time": row.get("book_observation_time", ""),
                "selected_source_row": row.get("book_source_row_number", ""),
                "latest_eligible_trade_time": row.get("latest_trade_observation_time", ""),
                "next_future_book_event_time": next_state_time,
                "next_future_trade_time": next_trade_time,
            }
        )
    return audits


def _deterministic_sample_indexes(length: int, sample_count: int) -> list[int]:
    if length <= sample_count:
        return list(range(length))
    if sample_count == 1:
        return [0]
    return sorted({round(i * (length - 1) / (sample_count - 1)) for i in range(sample_count)})
