# Phase 16 Performance Engineering

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

The frozen plan hash is `70fc7a9f1dc3fd80642d0dd83b8d09ba17fd3011be23ba432b92a788a539b350`. Benchmarks used
non-2026 bounded development/engineering fixtures, three repetitions,
`time.perf_counter`, `time.process_time`, best-effort peak RSS, and compact
`cProfile` summaries.

## Results

Phase 5 feature-engineering median speedup was
`5.108x`. Representative orchestration
median speedup was `4.939x`.
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
