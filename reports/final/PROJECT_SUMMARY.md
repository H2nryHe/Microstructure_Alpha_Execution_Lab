# Project Summary

## Problem

This project asks whether BTC-USDT order-book and order-flow signals can
forecast short-horizon future mid-price movement, and whether that predictive
structure survives the operational frictions required to trade it. The goal was
not to publish a profitability claim. The goal was to build a reproducible
microstructure research system that keeps market-data causality, execution
mechanics, transaction costs, and robustness checks in the same chain.

## What I Built

The repository implements a full research pipeline from immutable raw market
data through QA, order-book replay, causal research tables, leakage-controlled
features, forward labels, statistical research, predictive modeling,
walk-forward evaluation, signal construction, execution simulation, accounting,
cost and latency analysis, robustness analysis, final reporting, and performance
engineering. The canonical instrument is `BTC-USDT`, with explicit vendor symbol
mapping to Binance/Tardis `BTCUSDT`.

## Technical Challenges

The central engineering challenge was preserving what the research process could
have known at each timestamp. Exchange timestamps are retained, but local
observation time and source order drive replay and feature eligibility. Features
are backward-looking from each cutoff, labels are strictly forward-looking, and
cross-day feature or label leakage is disabled. The data layer preserves raw
source bytes, records checksums, and validates schema, duplicate, timestamp,
price, quantity, sequence, book-crossing, update-gap, and stale-BBO issues.

## Key Results

Queue imbalance produced a strong and stable 1s predictive rank signal across
the pre-registered development sample, with about 0.43 mean daily Spearman IC
and 24/24 positive development dates. OFI and multivariate modeling added
incremental information: Extended LightGBM produced +0.0107 incremental IC
across 18 expanding walk-forward folds. The modeling result was useful but
modest; QI remained the dominant simple signal.

## Execution Reality

Better predictive IC did not automatically produce better executable economics.
Signals are desired states, not fills. Market orders filled reliably but paid
spread, latency, and displayed-depth costs. Passive orders exposed queue
uncertainty, low fill participation, adverse selection, and residual inventory.
Cross-date economic robustness favored the simpler QI efficiency baseline more
often than the larger multivariate signal, and generic fee stresses showed thin
transaction-cost headroom.

## Engineering Quality

The project emphasizes deterministic artifacts and reviewable controls. Public
reports include source-traceable metrics, deterministic hashes, CI status, and
explicit limitations. Phase 16 profiled the existing pipeline before
optimization and found repeated trailing-window aggregation as a material Phase
5 bottleneck. A Python accumulator refactor reduced representative
feature-engineering median runtime from 1.493s to 0.292s, a 5.11x speedup, while
preserving an exact reference-vs-optimized feature output SHA-256.

## Limitations

This is a BTC-USDT historical research system using Binance/Tardis
reconstruction. It uses displayed book data only, does not observe hidden
liquidity, does not model endogenous self-impact, uses a simplified passive
queue model, and applies generic fee stress scenarios rather than venue-specific
live fee schedules. Execution robustness uses bounded development-date samples.
The 2026 temporal holdout remains untouched and reserved for future
confirmatory evaluation.
