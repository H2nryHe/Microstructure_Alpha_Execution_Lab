# Phase 14 Robustness Reports

Phase 14 tests whether Phase 11-13 execution and cost conclusions are stable
outside the single original execution date. Date selection is mechanical and
frozen before results.

- Phase 14 plan hash: `a0315262cb252c9e8b0bb0d63891e92cdfe5d16d0d7924cc570dc49b64107317`
- Phase 14 robustness artifact hash: `af685ef974b6cc5fd21a0c3ffe24fff6ff088f185ae8b32a337d25942c058379`
- Phase 14 results hash: recorded in `phase14_summary.json`
- Primary market dates: `2024-07-01, 2024-10-01, 2025-01-01, 2025-04-01, 2025-07-01, 2025-10-01`
- Passive dates: `2024-07-01, 2025-01-01, 2025-07-01`

The outputs retain negative days, low passive fill rates, residual inventory,
and model underperformance cases. No 2026 holdout data, annualized metrics,
Sharpe ratio, model tuning, signal retuning, or strategy optimization is
reported.

`market_date_level_summary.csv` contains mean, median, minimum, maximum, and
positive/negative day counts by model, latency, and fee overlay.
