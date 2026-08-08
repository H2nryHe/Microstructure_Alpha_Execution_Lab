"""Phase 6 label generation.

Labels use ``feature_cutoff_time`` as prediction time ``T``. Future observations
are selected by causal observation/cutoff time, never by exchange event time.
"""

from __future__ import annotations

import csv
import json
import math
import statistics
import time
from bisect import bisect_left, bisect_right
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from microalpha.labels.metadata import LABEL_VERSION, horizon_name, label_definitions
from microalpha.research.dataset import dataset_hash, iso, parse_iso_utc

MISSING = ""
DIRECTION_UP = "UP"
DIRECTION_DOWN = "DOWN"
DIRECTION_FLAT = "FLAT"


@dataclass(frozen=True)
class LabelConfig:
    label_version: str = LABEL_VERSION
    horizons_ms: tuple[int, ...] = (100, 500, 1000, 5000, 30000)
    classification_threshold_bps: Decimal = Decimal("0.5")
    future_lookup_rule: str = "first_observation_at_or_after_horizon"
    max_label_delay_ms: int = 100
    next_mid_change_max_search_ms: int = 30000
    cross_session_labels: bool = False
    include_lineage_columns: bool = True

    @classmethod
    def from_mapping(cls, values: dict[str, object]) -> "LabelConfig":
        horizons = tuple(int(value) for value in values.get("horizons_ms", cls.horizons_ms))
        threshold = Decimal(str(values.get("classification_threshold_bps", "0.5")))
        return cls(
            label_version=str(values.get("label_version", LABEL_VERSION)),
            horizons_ms=horizons,
            classification_threshold_bps=threshold,
            future_lookup_rule=str(
                values.get("future_lookup_rule", "first_observation_at_or_after_horizon")
            ),
            max_label_delay_ms=int(values.get("max_label_delay_ms", 100)),
            next_mid_change_max_search_ms=int(
                values.get("next_mid_change_max_search_ms", 30000)
            ),
            cross_session_labels=bool(values.get("cross_session_labels", False)),
            include_lineage_columns=bool(values.get("include_lineage_columns", True)),
        )


@dataclass(frozen=True)
class ValidState:
    row_index: int
    cutoff_time: datetime
    mid: Decimal
    spread: Optional[Decimal]
    session: str


@dataclass
class LabelBuildStats:
    total_rows: int
    label_column_count: int
    output_hash: str
    processing_time_seconds: float
    label_version: str
    metadata: list[dict[str, object]] = field(default_factory=list)
    summary: dict[str, dict[str, object]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def read_research_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def _decimal(value: Optional[str]) -> Optional[Decimal]:
    if value in (None, "", "nan", "NaN"):
        return None
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def _format_decimal(value: Optional[Decimal]) -> str:
    if value is None:
        return MISSING
    if value.is_nan():
        return "NaN"
    return format(value, "f")


def _format_float(value: Optional[float]) -> str:
    if value is None or math.isnan(value):
        return MISSING
    return repr(float(value))


def _session_key(timestamp: datetime) -> str:
    return timestamp.date().isoformat()


def _is_valid_state(row: dict[str, str]) -> bool:
    if row.get("is_available") == "false":
        return False
    mid = _decimal(row.get("mid"))
    return mid is not None and mid > 0


def _valid_state(row: dict[str, str], row_index: int) -> Optional[ValidState]:
    if not _is_valid_state(row):
        return None
    cutoff = parse_iso_utc(row["feature_cutoff_time"])
    return ValidState(
        row_index=row_index,
        cutoff_time=cutoff,
        mid=Decimal(row["mid"]),
        spread=_decimal(row.get("spread")),
        session=_session_key(cutoff),
    )


def _validate_input_order(rows: list[dict[str, str]]) -> None:
    previous: Optional[datetime] = None
    for index, row in enumerate(rows):
        cutoff = parse_iso_utc(row["feature_cutoff_time"])
        if previous is not None and cutoff < previous:
            raise ValueError(
                "Research rows must be monotonic by feature_cutoff_time in source order; "
                f"row {index + 1} moves backward from {iso(previous)} to {iso(cutoff)}."
            )
        previous = cutoff


def _base_fieldnames() -> list[str]:
    return [
        "label_version",
        "instrument",
        "observation_time",
        "feature_cutoff_time",
        "is_available",
        "mid",
        "spread",
        "book_observation_time",
        "book_event_time",
        "book_source_row_number",
    ]


def _label_fieldnames(config: LabelConfig) -> list[str]:
    fields = _base_fieldnames()
    for horizon_ms in config.horizons_ms:
        suffix = horizon_name(horizon_ms)
        fields.extend(
            [
                f"target_time_{suffix}",
                f"actual_label_time_{suffix}",
                f"label_delay_ms_{suffix}",
                f"ret_fwd_{suffix}",
                f"direction_{suffix}",
                f"future_mid_move_bps_{suffix}",
                f"future_move_in_spreads_{suffix}",
            ]
        )
    fields.extend([
        "next_mid_change_available",
        "next_mid_change_direction",
        "time_to_next_mid_change_ms",
    ])
    return fields


def classify_return(return_value: Decimal, threshold_bps: Decimal) -> str:
    threshold = threshold_bps / Decimal("10000")
    if return_value > threshold:
        return DIRECTION_UP
    if return_value < -threshold:
        return DIRECTION_DOWN
    return DIRECTION_FLAT


def _elapsed_ms(start: datetime, end: datetime) -> int:
    return int(round((end - start).total_seconds() * 1000))


def _future_state(
    *,
    valid_states: list[ValidState],
    valid_times: list[datetime],
    current_session: str,
    target_time: datetime,
    config: LabelConfig,
) -> Optional[ValidState]:
    if config.future_lookup_rule != "first_observation_at_or_after_horizon":
        raise ValueError(f"Unsupported future lookup rule: {config.future_lookup_rule}")
    if not config.cross_session_labels and _session_key(target_time) != current_session:
        return None
    index = bisect_left(valid_times, target_time)
    if index >= len(valid_states):
        return None
    candidate = valid_states[index]
    if not config.cross_session_labels and candidate.session != current_session:
        return None
    if _elapsed_ms(target_time, candidate.cutoff_time) > config.max_label_delay_ms:
        return None
    return candidate


def _next_mid_change(
    *,
    valid_states: list[ValidState],
    valid_times: list[datetime],
    current_state: ValidState,
    config: LabelConfig,
) -> tuple[str, str, str]:
    search_end = current_state.cutoff_time + timedelta(
        milliseconds=config.next_mid_change_max_search_ms
    )
    if not config.cross_session_labels and _session_key(search_end) != current_state.session:
        search_end = datetime.combine(
            current_state.cutoff_time.date(),
            datetime.max.time(),
            tzinfo=current_state.cutoff_time.tzinfo,
        )
    start_index = bisect_right(valid_times, current_state.cutoff_time)
    end_index = bisect_right(valid_times, search_end)
    for state in valid_states[start_index:end_index]:
        if not config.cross_session_labels and state.session != current_state.session:
            break
        if state.mid == current_state.mid:
            continue
        direction = "1" if state.mid > current_state.mid else "-1"
        return "true", direction, str(_elapsed_ms(current_state.cutoff_time, state.cutoff_time))
    return "false", MISSING, MISSING


def _label_values(
    *,
    current_state: Optional[ValidState],
    cutoff_time: datetime,
    valid_states: list[ValidState],
    valid_times: list[datetime],
    config: LabelConfig,
) -> dict[str, str]:
    values: dict[str, str] = {}
    for horizon_ms in config.horizons_ms:
        suffix = horizon_name(horizon_ms)
        target_time = cutoff_time + timedelta(milliseconds=horizon_ms)
        values[f"target_time_{suffix}"] = iso(target_time)
        values[f"actual_label_time_{suffix}"] = MISSING
        values[f"label_delay_ms_{suffix}"] = MISSING
        values[f"ret_fwd_{suffix}"] = MISSING
        values[f"direction_{suffix}"] = MISSING
        values[f"future_mid_move_bps_{suffix}"] = MISSING
        values[f"future_move_in_spreads_{suffix}"] = MISSING
        if current_state is None:
            continue
        future = _future_state(
            valid_states=valid_states,
            valid_times=valid_times,
            current_session=current_state.session,
            target_time=target_time,
            config=config,
        )
        if future is None:
            continue
        delay_ms = _elapsed_ms(target_time, future.cutoff_time)
        ret = math.log(float(future.mid) / float(current_state.mid))
        ret_decimal = Decimal(str(ret))
        mid_move = future.mid - current_state.mid
        spread_move = None
        if current_state.spread is not None and current_state.spread > 0:
            spread_move = mid_move / current_state.spread
        values[f"actual_label_time_{suffix}"] = iso(future.cutoff_time)
        values[f"label_delay_ms_{suffix}"] = str(delay_ms)
        values[f"ret_fwd_{suffix}"] = _format_float(ret)
        values[f"direction_{suffix}"] = classify_return(
            ret_decimal,
            config.classification_threshold_bps,
        )
        values[f"future_mid_move_bps_{suffix}"] = _format_decimal(
            Decimal("10000") * mid_move / current_state.mid
        )
        values[f"future_move_in_spreads_{suffix}"] = _format_decimal(spread_move)
    if current_state is None:
        values["next_mid_change_available"] = "false"
        values["next_mid_change_direction"] = MISSING
        values["time_to_next_mid_change_ms"] = MISSING
    else:
        available, direction, delay = _next_mid_change(
            valid_states=valid_states,
            valid_times=valid_times,
            current_state=current_state,
            config=config,
        )
        values["next_mid_change_available"] = available
        values["next_mid_change_direction"] = direction
        values["time_to_next_mid_change_ms"] = delay
    return values


def build_label_table(
    *,
    research_path: str | Path,
    output_path: str | Path,
    config: LabelConfig,
) -> LabelBuildStats:
    start_time = time.perf_counter()
    rows = read_research_rows(research_path)
    _validate_input_order(rows)
    valid_states = [
        state
        for index, row in enumerate(rows)
        if (state := _valid_state(row, index)) is not None
    ]
    valid_times = [state.cutoff_time for state in valid_states]
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = _label_fieldnames(config)

    with output.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for index, row in enumerate(rows):
            cutoff_time = parse_iso_utc(row["feature_cutoff_time"])
            current_state = _valid_state(row, index)
            label_row = {
                "label_version": config.label_version,
                "instrument": row.get("instrument", ""),
                "observation_time": row.get("observation_time", ""),
                "feature_cutoff_time": row.get("feature_cutoff_time", ""),
                "is_available": row.get("is_available", ""),
                "mid": row.get("mid", ""),
                "spread": row.get("spread", ""),
                "book_observation_time": row.get("book_observation_time", ""),
                "book_event_time": row.get("book_event_time", ""),
                "book_source_row_number": row.get("book_source_row_number", ""),
            }
            label_row.update(
                _label_values(
                    current_state=current_state,
                    cutoff_time=cutoff_time,
                    valid_states=valid_states,
                    valid_times=valid_times,
                    config=config,
                )
            )
            writer.writerow(label_row)

    metadata = [
        definition.to_dict()
        for definition in label_definitions(
            horizons_ms=config.horizons_ms,
            future_lookup_rule=config.future_lookup_rule,
            max_label_delay_ms=config.max_label_delay_ms,
            classification_threshold_bps=format(config.classification_threshold_bps, "f"),
        )
    ]
    summary = summarize_label_file(output, config)
    return LabelBuildStats(
        total_rows=len(rows),
        label_column_count=len(fieldnames) - len(_base_fieldnames()),
        output_hash=dataset_hash(output),
        processing_time_seconds=time.perf_counter() - start_time,
        label_version=config.label_version,
        metadata=metadata,
        summary=summary,
    )


def write_label_summary(stats: LabelBuildStats, output_path: str | Path) -> None:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(
        json.dumps(stats.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _percentile(sorted_values: list[float], percentile: float) -> Optional[float]:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (len(sorted_values) - 1) * percentile / 100.0
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return sorted_values[lower]
    weight = rank - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def _numeric_stats(values: list[float]) -> dict[str, Optional[float]]:
    values = sorted(values)
    if not values:
        return {
            "min": None,
            "p1": None,
            "p5": None,
            "median": None,
            "p95": None,
            "p99": None,
            "max": None,
            "mean": None,
            "std": None,
        }
    return {
        "min": values[0],
        "p1": _percentile(values, 1),
        "p5": _percentile(values, 5),
        "median": _percentile(values, 50),
        "p95": _percentile(values, 95),
        "p99": _percentile(values, 99),
        "max": values[-1],
        "mean": statistics.fmean(values),
        "std": statistics.pstdev(values) if len(values) > 1 else 0.0,
    }


def summarize_label_file(path: str | Path, config: LabelConfig) -> dict[str, dict[str, object]]:
    returns = {horizon_name(horizon): [] for horizon in config.horizons_ms}
    delays = {horizon_name(horizon): [] for horizon in config.horizons_ms}
    direction_counts = {
        horizon_name(horizon): {DIRECTION_UP: 0, DIRECTION_FLAT: 0, DIRECTION_DOWN: 0}
        for horizon in config.horizons_ms
    }
    total_rows = 0
    with Path(path).open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            total_rows += 1
            for horizon_ms in config.horizons_ms:
                suffix = horizon_name(horizon_ms)
                ret = row.get(f"ret_fwd_{suffix}", "")
                if ret not in ("", "NaN"):
                    returns[suffix].append(float(ret))
                delay = row.get(f"label_delay_ms_{suffix}", "")
                if delay:
                    delays[suffix].append(float(delay))
                direction = row.get(f"direction_{suffix}", "")
                if direction in direction_counts[suffix]:
                    direction_counts[suffix][direction] += 1

    summary: dict[str, dict[str, object]] = {}
    for horizon_ms in config.horizons_ms:
        suffix = horizon_name(horizon_ms)
        valid_regression = len(returns[suffix])
        missing_regression = total_rows - valid_regression
        valid_classification = sum(direction_counts[suffix].values())
        missing_classification = total_rows - valid_classification
        delay_values = sorted(delays[suffix])
        counts = direction_counts[suffix]
        summary[suffix] = {
            "total_rows": total_rows,
            "valid_regression_labels": valid_regression,
            "missing_regression_labels": missing_regression,
            "missing_regression_pct": (
                missing_regression / total_rows * 100.0 if total_rows else 0.0
            ),
            "valid_classification_labels": valid_classification,
            "missing_classification_labels": missing_classification,
            "missing_classification_pct": (
                missing_classification / total_rows * 100.0 if total_rows else 0.0
            ),
            "accepted_delay_ms": {
                "median": _percentile(delay_values, 50),
                "p95": _percentile(delay_values, 95),
                "max": delay_values[-1] if delay_values else None,
            },
            "direction_counts": counts,
            "direction_pct": {
                name: count / valid_classification * 100.0 if valid_classification else 0.0
                for name, count in counts.items()
            },
            "return_stats": _numeric_stats(returns[suffix]),
        }
    return summary
