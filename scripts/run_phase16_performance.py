"""Run Phase 16 bounded performance benchmarks and reports."""

from __future__ import annotations

import argparse
import cProfile
import csv
import io
import json
import math
import pstats
import resource
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from microalpha.book.replay import BookEvent, replay_events
from microalpha.execution.simulator import (
    BookSnapshot,
    OrderRequest,
    TradePrint,
    artifact_hash,
    execute_market_order,
    make_order_id,
    simulate_limit_order,
)
from microalpha.features.engineering import FeatureConfig, build_feature_table
from microalpha.research.dataset import dataset_hash
from microalpha.research.phase16 import (
    PHASE16_PERFORMANCE_PLAN_HASH,
    build_feature_table_reference,
    phase16_benchmark_artifact_hash,
    phase16_results_hash,
    validate_phase16_plan,
)

ENVIRONMENT_ID = "local_mac_python310_phase16"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="reports/phase16")
    parser.add_argument("--work-root", default="/tmp/microalpha-phase16")
    parser.add_argument("--repetitions", type=int, default=3)
    return parser.parse_args()


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def ru_maxrss_mb() -> float:
    usage = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return usage / (1024.0 * 1024.0) if sys.platform == "darwin" else usage / 1024.0


def write_csv_rows(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def feature_state_fields(depth: int) -> list[str]:
    fields = [
        "instrument",
        "observation_time",
        "feature_cutoff_time",
        "is_available",
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
    ]
    for index in range(1, depth + 1):
        fields.extend([f"bid_px_{index}", f"bid_sz_{index}"])
    for index in range(1, depth + 1):
        fields.extend([f"ask_px_{index}", f"ask_sz_{index}"])
    return fields


def state_row(base: datetime, index: int, *, depth: int = 10) -> dict[str, str]:
    timestamp = base + timedelta(milliseconds=50 * index)
    bid = Decimal("50000") + Decimal(index % 17) / Decimal("10")
    ask = bid + Decimal("1")
    bid_size = Decimal("1") + Decimal(index % 9) / Decimal("10")
    ask_size = Decimal("1.2") + Decimal(index % 7) / Decimal("10")
    row = {
        "instrument": "BTC-USDT",
        "observation_time": iso(timestamp),
        "feature_cutoff_time": iso(timestamp),
        "is_available": "true",
        "book_event_time": iso(timestamp),
        "book_observation_time": iso(timestamp),
        "book_source_row_number": str(index + 1),
        "best_bid": str(bid),
        "bid_sz_1": str(bid_size),
        "best_ask": str(ask),
        "ask_sz_1": str(ask_size),
        "mid": str((bid + ask) / Decimal("2")),
        "spread": str(ask - bid),
        "latest_trade_event_time": "",
        "latest_trade_observation_time": "",
    }
    for level in range(1, depth + 1):
        row[f"bid_px_{level}"] = str(bid - Decimal(level - 1))
        row[f"bid_sz_{level}"] = str(bid_size + Decimal(level) / Decimal("10"))
        row[f"ask_px_{level}"] = str(ask + Decimal(level - 1))
        row[f"ask_sz_{level}"] = str(ask_size + Decimal(level) / Decimal("10"))
    return row


def generate_phase5_fixture(root: Path, *, fixed_rows: int = 900) -> dict[str, Path | int]:
    fixture = root / "fixtures" / "phase5"
    fixture.mkdir(parents=True, exist_ok=True)
    base = datetime(2024, 7, 1, tzinfo=timezone.utc)
    depth = 10
    state_count = fixed_rows * 2 + 1
    states = [state_row(base, index, depth=depth) for index in range(state_count)]
    fixed = [states[index * 2] for index in range(fixed_rows)]
    trades = []
    for index in range(fixed_rows * 10):
        timestamp = base + timedelta(milliseconds=10 * index)
        price = Decimal("50000") + Decimal(index % 23) / Decimal("10")
        quantity = Decimal("0.01") + Decimal(index % 11) / Decimal("1000")
        trades.append(
            {
                "source_row_number": str(index + 1),
                "event_time": iso(timestamp),
                "receive_time": iso(timestamp),
                "price": str(price),
                "quantity": str(quantity),
                "side": "buy" if index % 2 == 0 else "sell",
                "trade_id": str(index + 1),
            }
        )
    state_path = fixture / "event_states.csv"
    fixed_path = fixture / "fixed_clock.csv"
    trades_path = fixture / "trades.csv"
    write_csv_rows(state_path, states, feature_state_fields(depth))
    write_csv_rows(fixed_path, fixed, feature_state_fields(depth))
    write_csv_rows(
        trades_path,
        trades,
        [
            "source_row_number",
            "event_time",
            "receive_time",
            "price",
            "quantity",
            "side",
            "trade_id",
        ],
    )
    return {
        "fixed": fixed_path,
        "states": state_path,
        "trades": trades_path,
        "fixed_rows": len(fixed),
        "state_rows": len(states),
        "trade_rows": len(trades),
    }


def generate_book_events(count: int = 2500) -> list[BookEvent]:
    base = datetime(2019, 12, 1, tzinfo=timezone.utc)
    rows: list[BookEvent] = []
    source_row = 1
    receive = iso(base)
    for level in range(10):
        rows.append(
            BookEvent(
                source_row,
                receive,
                receive,
                "bid",
                Decimal("7540") - Decimal(level),
                Decimal("1") + Decimal(level) / Decimal("10"),
                "snapshot",
                source_row,
            )
        )
        source_row += 1
        rows.append(
            BookEvent(
                source_row,
                receive,
                receive,
                "ask",
                Decimal("7541") + Decimal(level),
                Decimal("1.1") + Decimal(level) / Decimal("10"),
                "snapshot",
                source_row,
            )
        )
        source_row += 1
    for index in range(count):
        timestamp = iso(base + timedelta(milliseconds=5 * (index + 1)))
        side = "bid" if index % 2 == 0 else "ask"
        price = (
            Decimal("7540") - Decimal(index % 10)
            if side == "bid"
            else Decimal("7541") + Decimal(index % 10)
        )
        rows.append(
            BookEvent(
                source_row,
                timestamp,
                timestamp,
                side,
                price,
                Decimal("0.5") + Decimal(index % 13) / Decimal("10"),
                "set",
                source_row,
            )
        )
        source_row += 1
    return rows


def execution_order(index: int, *, order_type: str, side: str, arrival: datetime) -> OrderRequest:
    quantity = 0.05 + (index % 5) * 0.01
    limit_price = 50000.0 if side == "BUY" else 50001.0
    return OrderRequest(
        order_id=make_order_id("phase16", order_type, side, index),
        date="2024-07-01",
        signal_id=f"sig-{index}",
        model="phase16_fixture",
        side=side,  # type: ignore[arg-type]
        order_type=order_type,  # type: ignore[arg-type]
        quantity=quantity,
        limit_price=None if order_type == "MARKET" else limit_price,
        order_create_time=arrival,
        order_arrival_time=arrival,
        remaining_quantity=quantity,
        expiration_time=arrival + timedelta(milliseconds=1000) if order_type == "LIMIT" else None,
    )


def execution_snapshot(arrival: datetime) -> BookSnapshot:
    return BookSnapshot(
        observation_time=arrival,
        bids=((50000.0, 3.0), (49999.0, 3.0), (49998.0, 3.0)),
        asks=((50001.0, 3.0), (50002.0, 3.0), (50003.0, 3.0)),
    )


def run_market_fixture(order_count: int = 3000) -> dict[str, Any]:
    base = datetime(2024, 7, 1, tzinfo=timezone.utc)
    rows = []
    fills = []
    for index in range(order_count):
        arrival = base + timedelta(milliseconds=index)
        side = "BUY" if index % 2 == 0 else "SELL"
        order = execution_order(index, order_type="MARKET", side=side, arrival=arrival)
        result, child = execute_market_order(
            order,
            execution_snapshot(arrival),
            fee_bps=0.0,
            decision_mid=50000.5,
        )
        rows.append(result)
        fills.extend(child)
    return {"orders": rows, "fills": fills, "hash": artifact_hash([*rows, *fills])}


def run_passive_fixture(order_count: int = 800) -> dict[str, Any]:
    base = datetime(2024, 7, 1, tzinfo=timezone.utc)
    rows = []
    fills = []
    for index in range(order_count):
        arrival = base + timedelta(milliseconds=2 * index)
        side = "BUY" if index % 2 == 0 else "SELL"
        order = execution_order(index, order_type="LIMIT", side=side, arrival=arrival)
        trade_side = "sell" if side == "BUY" else "buy"
        price = 50000.0 if side == "BUY" else 50001.0
        trades = [
            TradePrint(arrival + timedelta(milliseconds=10 * step), trade_side, price, 0.04)
            for step in range(1, 8)
        ]
        result, child = simulate_limit_order(
            order,
            execution_snapshot(arrival),
            trades,
            fee_bps=0.0,
            queue_fraction=0.0,
            decision_mid=50000.5,
        )
        rows.append(result)
        fills.extend(child)
    return {"orders": rows, "fills": fills, "hash": artifact_hash([*rows, *fills])}


def timed_call(func: Callable[[], Any]) -> tuple[Any, float, float, float]:
    rss_before = ru_maxrss_mb()
    cpu_start = time.process_time()
    start = time.perf_counter()
    result = func()
    runtime = time.perf_counter() - start
    cpu = time.process_time() - cpu_start
    return result, runtime, cpu, max(rss_before, ru_maxrss_mb())


def profile_call(func: Callable[[], Any], *, stage: str) -> list[dict[str, Any]]:
    profile = cProfile.Profile()
    start = time.perf_counter()
    profile.enable()
    func()
    profile.disable()
    runtime = max(time.perf_counter() - start, 1e-12)
    stats = pstats.Stats(profile, stream=io.StringIO()).strip_dirs().sort_stats("cumulative")
    rows = []
    for function, values in sorted(
        stats.stats.items(),
        key=lambda item: item[1][3],
        reverse=True,
    )[:8]:
        call_count, _primitive, self_time, cumulative, _callers = values
        filename, line, name = function
        rows.append(
            {
                "stage": stage,
                "function/module": f"{filename}:{line}:{name}",
                "cumulative time": cumulative,
                "self time": self_time,
                "call count": call_count,
                "fraction of stage runtime": cumulative / runtime,
                "optimization candidate": "yes"
                if stage == "phase5_feature_engineering" and "engineering.py" in filename
                else "no",
                "reason": "measured Python window aggregation path"
                if stage == "phase5_feature_engineering" and "engineering.py" in filename
                else "profiled for Phase 16 baseline evidence",
            }
        )
    return rows


def benchmark_rows(
    *,
    repetitions: int,
    stage: str,
    dataset: str,
    input_rows: int,
    output_rows: Callable[[Any], int],
    implementation: str,
    func: Callable[[int], Any],
) -> list[dict[str, Any]]:
    rows = []
    for repeat_id in range(1, repetitions + 1):
        result, runtime, cpu, rss = timed_call(lambda repeat_id=repeat_id: func(repeat_id))
        rows.append(
            {
                "stage": stage,
                "dataset/date": dataset,
                "input rows/events": input_rows,
                "output rows": output_rows(result),
                "runtime_seconds": runtime,
                "cpu_time_seconds": cpu,
                "throughput_rows_per_sec": input_rows / runtime if runtime > 0 else math.inf,
                "peak_memory_mb if available": rss,
                "repeat_id": repeat_id,
                "environment_id": ENVIRONMENT_ID,
                "implementation": implementation,
            }
        )
    return rows


def medians(rows: list[dict[str, Any]]) -> dict[str, float]:
    grouped: dict[str, list[float]] = {}
    for row in rows:
        grouped.setdefault(str(row["stage"]), []).append(float(row["runtime_seconds"]))
    return {stage: statistics.median(values) for stage, values in sorted(grouped.items())}


def write_figures(
    output_dir: Path,
    baseline: list[dict[str, Any]],
    optimized: list[dict[str, Any]],
    hotspots: list[dict[str, Any]],
) -> None:
    figures = output_dir / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    baseline_medians = medians(baseline)
    optimized_medians = medians(optimized)
    stages = list(baseline_medians)
    x = range(len(stages))

    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.bar(
        [value - 0.18 for value in x],
        [baseline_medians[s] for s in stages],
        width=0.36,
        label="baseline",
    )
    ax.bar(
        [value + 0.18 for value in x],
        [optimized_medians[s] for s in stages],
        width=0.36,
        label="optimized",
    )
    ax.set_xticks(list(x), stages, rotation=25, ha="right")
    ax.set_ylabel("median runtime seconds")
    ax.legend()
    fig.tight_layout()
    fig.savefig(figures / "baseline_vs_optimized_runtime_by_stage.png", dpi=160)
    plt.close(fig)

    baseline_tp = {
        row["stage"]: statistics.median(
            [
                float(item["throughput_rows_per_sec"])
                for item in baseline
                if item["stage"] == row["stage"]
            ]
        )
        for row in baseline
    }
    optimized_tp = {
        row["stage"]: statistics.median(
            [
                float(item["throughput_rows_per_sec"])
                for item in optimized
                if item["stage"] == row["stage"]
            ]
        )
        for row in optimized
    }
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.bar(
        [value - 0.18 for value in x],
        [baseline_tp[s] for s in stages],
        width=0.36,
        label="baseline",
    )
    ax.bar(
        [value + 0.18 for value in x],
        [optimized_tp[s] for s in stages],
        width=0.36,
        label="optimized",
    )
    ax.set_xticks(list(x), stages, rotation=25, ha="right")
    ax.set_ylabel("median rows/events per second")
    ax.legend()
    fig.tight_layout()
    fig.savefig(figures / "throughput_before_vs_after.png", dpi=160)
    plt.close(fig)

    top = sorted(hotspots, key=lambda row: float(row["cumulative time"]), reverse=True)[:8]
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.barh(
        [row["function/module"] for row in reversed(top)],
        [float(row["fraction of stage runtime"]) for row in reversed(top)],
    )
    ax.set_xlabel("fraction of profiled stage runtime")
    fig.tight_layout()
    fig.savefig(figures / "hotspot_runtime_contribution.png", dpi=160)
    plt.close(fig)


def markdown_report(summary: dict[str, Any]) -> str:
    speedups = summary["speedups"]
    return f"""# Phase 16 Performance Engineering

## Measured Bottlenecks

Bounded profiling covered Phase 3 replay, Phase 5 feature engineering,
Phase 11 market execution, Phase 11 passive execution, and a representative
two-date orchestration path. The material optimization target was Phase 5
trailing-window aggregation, where the frozen reference recomputed OFI and
trade aggregates by scanning every event in each active window at every cutoff.

## Why Each Target Was Selected

The Phase 5 window path is central to full-day research builds and had the
clearest Python-level repeated-work pattern. Phase 3 replay and Phase 11
execution were profiled and benchmarked, but no code was changed there in
Phase 16.

## Optimization Design

The optimized Phase 5 path keeps the existing causal membership rule
`(T-W, T]`. It now maintains per-window running sums and counts as events enter
and leave each trailing window, replacing repeated aggregation scans with
deterministic accumulator updates.

## Correctness Preservation

The frozen reference implementation remains available through Phase 16 test and
benchmark helpers. Optimized and reference feature CSVs matched exactly by
SHA-256 on the bounded audit fixture. Execution and replay paths were not
modified; deterministic replay and execution artifact hashes were still checked.

## Benchmark Methodology

The frozen plan hash is `{PHASE16_PERFORMANCE_PLAN_HASH}`. Benchmarks used
non-2026 bounded development/engineering fixtures, three repetitions,
`time.perf_counter`, `time.process_time`, best-effort peak RSS, and compact
`cProfile` summaries.

## Results

Phase 5 feature-engineering median speedup was
`{speedups["phase5_feature_engineering"]:.3f}x`. Representative orchestration
median speedup was `{speedups["representative_multidate_orchestration"]:.3f}x`.
See `baseline_benchmarks.csv`, `optimized_benchmarks.csv`, and the figures in
`figures/`.

## Complexity Discussion

Before: trailing window aggregation performed repeated per-cutoff scans across
active window contents, approximately `O(Q * W_active)` for each window family.

After: each event is added and removed once per configured window, while each
cutoff reads maintained totals, approximately `O(E * W + Q * W)` for the window
families. This preserves constants and semantics while removing repeated scans
over event contents.

## Tradeoffs

The accumulators add a small amount of mutable state inside the Phase 5 builder.
The state is local to one feature build, deterministic, and covered by
equivalence tests against the frozen reference path.

## Why C++ Was Or Was Not Used

C++ was not used. Profiling identified a Python algorithmic repeated-work issue
that was small and isolated enough to fix directly with clearer Python data
structures while preserving a simple Python fallback.
"""


def phase16_readme(summary: dict[str, Any]) -> str:
    return f"""# Phase 16 Artifacts

- Benchmark plan hash: `{PHASE16_PERFORMANCE_PLAN_HASH}`
- Benchmark artifact hash: `{summary["phase16_benchmark_artifact_hash"]}`
- Primary optimization: Phase 5 trailing-window accumulators
- C++ decision: not justified after profiling
- Holdout policy: no 2026 data accessed
"""


def main() -> int:
    args = parse_args()
    plan_hash = validate_phase16_plan()
    output_dir = Path(args.output_dir)
    work_root = Path(args.work_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    work_root.mkdir(parents=True, exist_ok=True)

    feature_fixture = generate_phase5_fixture(work_root)
    config = FeatureConfig()
    book_events = generate_book_events()

    def phase3() -> Any:
        return replay_events(book_events, depth=5)

    def phase5_reference(repeat_id: int) -> Any:
        return build_feature_table_reference(
            fixed_clock_path=feature_fixture["fixed"],
            event_state_path=feature_fixture["states"],
            trades_path=feature_fixture["trades"],
            output_path=work_root / f"reference_features_{repeat_id}.csv",
            config=config,
        )

    def phase5_optimized(repeat_id: int) -> Any:
        return build_feature_table(
            fixed_clock_path=feature_fixture["fixed"],
            event_state_path=feature_fixture["states"],
            trades_path=feature_fixture["trades"],
            output_path=work_root / f"optimized_features_{repeat_id}.csv",
            config=config,
        )

    def orchestration_reference(repeat_id: int) -> Any:
        replay = phase3()
        first = phase5_reference(repeat_id * 10)
        second = phase5_reference(repeat_id * 10 + 1)
        return {"rows": replay.rows_processed + first.total_rows + second.total_rows}

    def orchestration_optimized(repeat_id: int) -> Any:
        replay = phase3()
        first = phase5_optimized(repeat_id * 10)
        second = phase5_optimized(repeat_id * 10 + 1)
        return {"rows": replay.rows_processed + first.total_rows + second.total_rows}

    profile_rows = []
    profile_rows.extend(profile_call(phase3, stage="phase3_book_replay"))
    profile_rows.extend(
        profile_call(lambda: phase5_reference(0), stage="phase5_feature_engineering")
    )
    profile_rows.extend(profile_call(run_market_fixture, stage="phase11_market_execution"))
    profile_rows.extend(profile_call(run_passive_fixture, stage="phase11_passive_execution"))
    profile_rows.extend(
        profile_call(
            lambda: orchestration_reference(0),
            stage="representative_multidate_orchestration",
        )
    )

    baseline: list[dict[str, Any]] = []
    optimized: list[dict[str, Any]] = []
    repetitions = int(args.repetitions)
    feature_input_rows = int(feature_fixture["fixed_rows"]) + int(feature_fixture["trade_rows"])
    baseline.extend(
        benchmark_rows(
            repetitions=repetitions,
            stage="phase3_book_replay",
            dataset="2019-12-01 bounded book events",
            input_rows=len(book_events),
            output_rows=lambda result: int(result.rows_processed),
            implementation="reference",
            func=lambda _repeat: phase3(),
        )
    )
    optimized.extend(
        benchmark_rows(
            repetitions=repetitions,
            stage="phase3_book_replay",
            dataset="2019-12-01 bounded book events",
            input_rows=len(book_events),
            output_rows=lambda result: int(result.rows_processed),
            implementation="unchanged",
            func=lambda _repeat: phase3(),
        )
    )
    baseline.extend(
        benchmark_rows(
            repetitions=repetitions,
            stage="phase5_feature_engineering",
            dataset="2024-07-01 bounded feature fixture",
            input_rows=feature_input_rows,
            output_rows=lambda result: int(result.total_rows),
            implementation="reference",
            func=phase5_reference,
        )
    )
    optimized.extend(
        benchmark_rows(
            repetitions=repetitions,
            stage="phase5_feature_engineering",
            dataset="2024-07-01 bounded feature fixture",
            input_rows=feature_input_rows,
            output_rows=lambda result: int(result.total_rows),
            implementation="optimized",
            func=phase5_optimized,
        )
    )
    baseline.extend(
        benchmark_rows(
            repetitions=repetitions,
            stage="phase11_market_execution",
            dataset="2024-07-01 bounded market execution fixture",
            input_rows=3000,
            output_rows=lambda result: len(result["orders"]),
            implementation="reference",
            func=lambda _repeat: run_market_fixture(),
        )
    )
    optimized.extend(
        benchmark_rows(
            repetitions=repetitions,
            stage="phase11_market_execution",
            dataset="2024-07-01 bounded market execution fixture",
            input_rows=3000,
            output_rows=lambda result: len(result["orders"]),
            implementation="unchanged",
            func=lambda _repeat: run_market_fixture(),
        )
    )
    baseline.extend(
        benchmark_rows(
            repetitions=repetitions,
            stage="phase11_passive_execution",
            dataset="2024-07-01 bounded passive execution fixture",
            input_rows=800,
            output_rows=lambda result: len(result["orders"]),
            implementation="reference",
            func=lambda _repeat: run_passive_fixture(),
        )
    )
    optimized.extend(
        benchmark_rows(
            repetitions=repetitions,
            stage="phase11_passive_execution",
            dataset="2024-07-01 bounded passive execution fixture",
            input_rows=800,
            output_rows=lambda result: len(result["orders"]),
            implementation="unchanged",
            func=lambda _repeat: run_passive_fixture(),
        )
    )
    baseline.extend(
        benchmark_rows(
            repetitions=repetitions,
            stage="representative_multidate_orchestration",
            dataset="2019-12-01 plus 2024-07-01 bounded two-date path",
            input_rows=len(book_events) + 2 * feature_input_rows,
            output_rows=lambda result: int(result["rows"]),
            implementation="reference",
            func=orchestration_reference,
        )
    )
    optimized.extend(
        benchmark_rows(
            repetitions=repetitions,
            stage="representative_multidate_orchestration",
            dataset="2019-12-01 plus 2024-07-01 bounded two-date path",
            input_rows=len(book_events) + 2 * feature_input_rows,
            output_rows=lambda result: int(result["rows"]),
            implementation="optimized",
            func=orchestration_optimized,
        )
    )

    benchmark_fields = [
        "stage",
        "dataset/date",
        "input rows/events",
        "output rows",
        "runtime_seconds",
        "cpu_time_seconds",
        "throughput_rows_per_sec",
        "peak_memory_mb if available",
        "repeat_id",
        "environment_id",
        "implementation",
    ]
    hotspot_fields = [
        "stage",
        "function/module",
        "cumulative time",
        "self time",
        "call count",
        "fraction of stage runtime",
        "optimization candidate",
        "reason",
    ]
    write_csv_rows(output_dir / "baseline_benchmarks.csv", baseline, benchmark_fields)
    write_csv_rows(output_dir / "optimized_benchmarks.csv", optimized, benchmark_fields)
    write_csv_rows(output_dir / "profile_hotspots.csv", profile_rows, hotspot_fields)

    ref_stats = phase5_reference(99)
    opt_stats = phase5_optimized(99)
    ref_path = work_root / "reference_features_99.csv"
    opt_path = work_root / "optimized_features_99.csv"
    replay_a = phase3()
    replay_b = phase3()
    market_a = run_market_fixture(100)
    market_b = run_market_fixture(100)
    passive_a = run_passive_fixture(100)
    passive_b = run_passive_fixture(100)
    equivalence = [
        {
            "comparison": "phase5_reference_vs_optimized",
            "status": "PASS" if dataset_hash(ref_path) == dataset_hash(opt_path) else "FAIL",
            "reference_hash": ref_stats.output_hash,
            "optimized_hash": opt_stats.output_hash,
            "details": "exact feature CSV SHA-256 equality",
        },
        {
            "comparison": "phase3_deterministic_replay",
            "status": "PASS" if replay_a.output_hash == replay_b.output_hash else "FAIL",
            "reference_hash": replay_a.output_hash,
            "optimized_hash": replay_b.output_hash,
            "details": "book replay unchanged; repeated output hash equality",
        },
        {
            "comparison": "phase11_market_execution_deterministic",
            "status": "PASS" if market_a["hash"] == market_b["hash"] else "FAIL",
            "reference_hash": market_a["hash"],
            "optimized_hash": market_b["hash"],
            "details": "execution code unchanged; repeated artifact hash equality",
        },
        {
            "comparison": "phase11_passive_execution_deterministic",
            "status": "PASS" if passive_a["hash"] == passive_b["hash"] else "FAIL",
            "reference_hash": passive_a["hash"],
            "optimized_hash": passive_b["hash"],
            "details": "execution code unchanged; repeated artifact hash equality",
        },
    ]
    write_csv_rows(
        output_dir / "equivalence_results.csv",
        equivalence,
        ["comparison", "status", "reference_hash", "optimized_hash", "details"],
    )

    baseline_medians = medians(baseline)
    optimized_medians = medians(optimized)
    speedups = {
        stage: baseline_medians[stage] / optimized_medians[stage]
        for stage in baseline_medians
        if optimized_medians[stage] > 0
    }
    summary_without_hash = {
        "phase16_status": "PASS" if all(row["status"] == "PASS" for row in equivalence) else "FAIL",
        "phase16_performance_plan_hash": plan_hash,
        "phase15_commit_sha": "1eb43e516366c08165b5ac05d367d0bf342dd82e",
        "phase15_tests_run_id": 31417149603,
        "phase15_research_smoke_run_id": 31417149604,
        "no_2026_access": True,
        "optimization_targets": ["phase5_feature_engineering"],
        "optimization_version": "phase16_python_window_accumulators_v1",
        "cplusplus_decision": "not justified after profiling",
        "baseline_medians": baseline_medians,
        "optimized_medians": optimized_medians,
        "speedups": speedups,
        "equivalence": equivalence,
        "environment": {
            "environment_id": ENVIRONMENT_ID,
            "python": sys.version.split()[0],
        },
    }
    (output_dir / "PERFORMANCE_ENGINEERING.md").write_text(
        markdown_report(summary_without_hash), encoding="utf-8"
    )

    write_figures(output_dir, baseline, optimized, profile_rows)
    benchmark_hash = phase16_benchmark_artifact_hash(output_dir)
    final_summary = {
        **summary_without_hash,
        "phase16_benchmark_artifact_hash": benchmark_hash,
        "phase16_results_hash": "",
    }
    (output_dir / "README.md").write_text(phase16_readme(final_summary), encoding="utf-8")
    final_summary["phase16_results_hash"] = phase16_results_hash(output_dir, final_summary)
    (output_dir / "phase16_summary.json").write_text(
        json.dumps(final_summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(final_summary, indent=2, sort_keys=True))
    return 0 if final_summary["phase16_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
