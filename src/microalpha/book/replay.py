"""Order-book replay for Phase 3."""

from __future__ import annotations

import csv
import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Optional

from microalpha.book.state import BookStateError, OrderBook


@dataclass(frozen=True)
class BookEvent:
    source_row_number: int
    event_time: str
    receive_time: str
    side: str
    price: Decimal
    quantity: Decimal
    update_type: str
    sequence_id: Optional[int] = None


@dataclass
class ReplayStats:
    rows_processed: int = 0
    rows_ignored_before_snapshot: int = 0
    initial_snapshot_start_row: Optional[int] = None
    initial_snapshot_end_row: Optional[int] = None
    inserts: int = 0
    updates: int = 0
    deletions: int = 0
    noops: int = 0
    invalid_states: int = 0
    replay_resets: int = 0
    first_event_time: Optional[str] = None
    last_event_time: Optional[str] = None
    processing_time_seconds: float = 0.0
    final_state: dict[str, str] = field(default_factory=dict)
    output_hash: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _row_to_event(row: dict[str, str], fallback_row_number: int) -> BookEvent:
    sequence_value = row.get("sequence_id")
    sequence_id = int(sequence_value) if sequence_value not in (None, "") else None
    source_row = row.get("source_row_number")
    return BookEvent(
        source_row_number=int(source_row) if source_row not in (None, "") else fallback_row_number,
        event_time=row["event_time"],
        receive_time=row.get("receive_time", ""),
        side=row["side"].lower(),
        price=Decimal(row["price"]),
        quantity=Decimal(row["quantity"]),
        update_type=row.get("update_type", "set").lower(),
        sequence_id=sequence_id,
    )


def read_bronze_book_events(path: str | Path) -> Iterable[BookEvent]:
    with Path(path).open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        for fallback_row_number, row in enumerate(reader, start=1):
            yield _row_to_event(row, fallback_row_number)


def _group_key(event: BookEvent, ordering_mode: str) -> tuple[str, int]:
    if ordering_mode == "vendor_sequence" and event.sequence_id is not None:
        return ("sequence", event.sequence_id)
    receive_or_event_time = event.receive_time or event.event_time
    return ("capture", hash(receive_or_event_time))


def _event_group_id(event: BookEvent, ordering_mode: str) -> str:
    if ordering_mode == "vendor_sequence" and event.sequence_id is not None:
        return f"seq:{event.sequence_id}"
    return f"local:{event.receive_time or event.event_time}"


def _apply_event(book: OrderBook, event: BookEvent, stats: ReplayStats) -> None:
    action = book.apply_level(event.side, event.price, event.quantity)
    if action == "insert":
        stats.inserts += 1
    elif action == "update":
        stats.updates += 1
    elif action == "delete":
        stats.deletions += 1
    else:
        stats.noops += 1


def _validate_vendor_sequence(events: list[BookEvent]) -> None:
    previous: Optional[int] = None
    for event in events:
        if event.sequence_id is None:
            continue
        if previous is not None and event.sequence_id < previous:
            raise BookStateError("Vendor sequence IDs must be non-decreasing in source order")
        previous = event.sequence_id


def replay_events(
    events: Iterable[BookEvent],
    *,
    depth: int = 5,
    ordering_mode: str = "capture_order",
) -> ReplayStats:
    """Replay book events, validating after each logical source-message group."""

    start = time.perf_counter()
    materialized = list(events)
    if ordering_mode == "vendor_sequence":
        _validate_vendor_sequence(materialized)

    book = OrderBook()
    stats = ReplayStats()
    initialized = False
    current_group: list[BookEvent] = []
    current_group_id: Optional[str] = None

    def flush_group() -> None:
        nonlocal initialized
        if not current_group:
            return
        group_is_snapshot = any(event.update_type == "snapshot" for event in current_group)
        if group_is_snapshot:
            if initialized:
                stats.replay_resets += 1
            book.clear()
            if stats.initial_snapshot_start_row is None:
                stats.initial_snapshot_start_row = current_group[0].source_row_number
                stats.initial_snapshot_end_row = current_group[-1].source_row_number
            for event in current_group:
                _apply_event(book, event, stats)
            initialized = True
        elif initialized:
            for event in current_group:
                _apply_event(book, event, stats)
        else:
            stats.rows_ignored_before_snapshot += len(current_group)
            return
        try:
            book.metrics(depth=depth)
        except BookStateError:
            stats.invalid_states += 1
            raise

    previous_source_row: Optional[int] = None
    for event in materialized:
        stats.rows_processed += 1
        if previous_source_row is not None and event.source_row_number <= previous_source_row:
            raise BookStateError("Source row order is not strictly increasing")
        previous_source_row = event.source_row_number
        if stats.first_event_time is None:
            stats.first_event_time = event.event_time
        stats.last_event_time = event.event_time

        group_id = _event_group_id(event, ordering_mode)
        if current_group_id is None:
            current_group_id = group_id
        if group_id != current_group_id:
            flush_group()
            current_group = []
            current_group_id = group_id
        current_group.append(event)

    flush_group()
    stats.final_state = book.snapshot(depth=depth)
    stable_payload = json.dumps(stats.final_state, sort_keys=True).encode("utf-8")
    stats.output_hash = hashlib.sha256(stable_payload).hexdigest()
    stats.processing_time_seconds = time.perf_counter() - start
    return stats


def replay_bronze_book_csv(
    path: str | Path,
    *,
    depth: int = 5,
    ordering_mode: str = "capture_order",
) -> ReplayStats:
    return replay_events(
        read_bronze_book_events(path),
        depth=depth,
        ordering_mode=ordering_mode,
    )
