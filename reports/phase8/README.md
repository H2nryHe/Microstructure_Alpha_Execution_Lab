# Phase 8 Baseline Predictive Modeling

Status: PASS

Plan hash: `823ee7a98be9a5199842536a65edd2394a39f1c5c6ae13947c29bd7c1c2494fe`

Train: 2024 development dates. Validation: 2025 development dates.

Primary target: `ret_fwd_1s`.

Primary sample: deterministic non-overlapping 1s anchors from the 100ms grid.

The central benchmark is QI-only. Complex models are evaluated by incremental daily IC
relative to QI, not by absolute score alone.

No 2026 holdout data, trading threshold, fill simulation, PnL, or backtest is used.
