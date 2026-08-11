# Microstructure Alpha Execution Lab

**Research question:** Can BTC-USDT order-book and order-flow signals forecast
short-horizon future mid-price movement, and do those forecasts retain economic
value after causal market-data reconstruction, event-driven execution,
accounting, transaction costs, latency, and cross-date robustness tests?

## Research Question

The project tests the full chain from causal market-data reconstruction to
statistical prediction, signal construction, execution simulation, accounting,
and cost robustness. The goal is to determine whether short-horizon
microstructure predictability survives the mechanics required to trade it.

## Key Findings

- Queue imbalance showed strong, stable 1s predictive rank signal: 0.43 mean daily
  Spearman IC, with 24/24 development dates positive across ~20.7M observations.
- OFI added incremental information beyond QI; LightGBM improved predictive IC
  modestly and consistently, including +0.0107 Extended delta across 18
  expanding folds.
- Predictive lift did not automatically translate into better execution
  economics. Phase 13 found 0.376 bps QI reference breakeven, 0.253 bps
  QI+OFI reference breakeven, and 0.277 bps Extended reference breakeven.
- Cross-date robustness favored the simpler economic-efficiency baseline: QI
  ranked first in 8 first-place efficiency contexts, while Extended-minus-QI
  averaged -2443.68 mean Extended-minus-QI net delta in the 0.25 bps market
  cost scenario.
- Passive execution remained fill- and inventory-constrained: 1.56% QI passive
  mean fill rate, 4.70% Extended passive mean fill rate, and 4.79 mean Extended
  terminal position at 0ms in Phase 14 diagnostics.

![Architecture diagram](reports/final/figures/architecture_diagram.png)

## Verified Scale

- 6.49M L2 rows in the 2019 validation-day replay.
- 816K completed event states and 864K fixed 100ms observations.
- 91 features generated with observation-time leakage controls.
- 24 development dates from 2024-2025; 2026 remains an untouched temporal
  holdout.
- 18 expanding folds in chronological walk-forward evaluation.

## Architecture

```text
Tardis L2 + Trades
    -> Immutable Raw Data
    -> Market Data QA
    -> Order Book Replay
    -> Causal Research Dataset
    -> Microstructure Features
    -> Forward Labels
    -> Statistical Research
    -> Predictive Modeling
    -> Walk-Forward Evaluation
    -> Signal Construction
    -> Execution Simulator
    -> Accounting
    -> Cost / Latency Analysis
    -> Cross-Date Robustness
```

The strongest engineering choice is causal replay. Exchange event time is
preserved, but local observation time plus source order governs what the
research process could have known at each cutoff. Features are backward-looking
from T; labels are strictly after T; no cross-day feature or label leakage is
allowed.

## Data Integrity

The pipeline preserves raw source files as immutable byte streams, computes
checksums, writes metadata manifests, validates schemas, and records deterministic
hashes for major research artifacts. Market-data QA checks duplicates, invalid
prices or quantities, sequence issues where applicable, crossed or locked
books, update gaps, stale BBO, and discontinuities.

Source hardening used Binance official public trades for real trade ingestion
validation and Tardis normalized Binance incremental L2 for order-book replay.
The canonical internal instrument is `BTC-USDT`; the vendor symbol is
`BTCUSDT`.

## Signal Research

Top-of-book QI was the dominant simple signal. The Phase 7 robustness audit
attacked the strong headline IC rather than accepting it at face value:
changed-state checks produced 0.447 changed-state IC, non-overlap sampling
produced 0.425 non-overlap IC, and a five-minute temporal mismatch control
produced only 0.004 temporal mismatch IC.

OFI provided the clearest incremental information beyond QI. Microprice and
depth imbalance were useful but highly redundant with QI, which is why the
final interpretation separates interpretable signal families from raw feature
count.

## Predictive Modeling

Phase 8 found 0.422 QI baseline mean daily IC and +0.008 Extended LightGBM
lift, positive in 12/12 validation months. Phase 9 repeated the comparison
under chronological retraining: QI produced 0.432 QI mean IC, QI+OFI added
+0.0067 QI+OFI delta, and Extended added +0.0107 Extended delta across
18 expanding folds. The rolling-six-month comparison retained +0.0082
rolling-6 Extended delta.

The modeling conclusion is modest but real: nonlinear multivariate modeling
added stable predictive information, but QI remained the dominant ranking
signal.

## Execution Reality

Signals are desired states, not fills. The primary Phase 10 q10/q90 signal rule
produced 21.7% QI active coverage, 20.2% QI+OFI active coverage, and 17.3%
Extended active coverage. Market orders filled reliably but paid spread,
displayed-depth consumption, and latency through fill prices. Passive orders
introduced queue uncertainty, low fill rates, adverse selection, and residual
inventory.

Reference-day Phase 12 accounting showed 0.376 bps QI market 0ms, 0.252 bps
QI+OFI market 0ms, and 0.277 bps Extended market 0ms gross PnL per turnover.
Extended generated more gross dollars in that diagnostic, but QI had better
turnover efficiency.

## Cost / Robustness

Phase 13 showed that transaction-cost headroom was thin: no reference-day market
scenario remained net positive at a 0.50 bps fee overlay. Phase 14 then froze a
six-date robustness plan before evaluating new date outcomes. QI had 0.687 bps
QI mean breakeven at 0ms and 0.440 bps QI median breakeven at 0ms, but only
3/6 QI net-positive days at 0.25 bps and 2/6 QI net-positive days at 0.50 bps.

The cross-date conclusion is that strong predictive structure existed, but the
economic edge was materially eroded by turnover, latency, costs, fill
uncertainty, and inventory.

## Performance Engineering

Phase 16 profiled bounded replay, feature, execution, and representative
orchestration paths without opening the 2026 holdout. The targeted Phase 5
trailing-window accumulator change reduced bounded feature-engineering median
runtime from 1.493s to 0.292s, a 5.11x speedup, with exact feature CSV hash
equivalence against the frozen reference implementation.

## Reproduce

Python 3.11 or newer is expected.

```bash
python -m pip install -e ".[dev]"
python -m pytest
ruff check src tests scripts
python -m compileall -q src scripts tests
microalpha-smoke --manifest-out /tmp/microalpha-smoke.yaml
```

Final report artifacts:

- [Final research report](reports/final/MICROSTRUCTURE_ALPHA_EXECUTION_LAB_REPORT.md)
- [Final metrics registry](reports/final/FINAL_METRICS.json)
- [Final artifact index](reports/final/FINAL_ARTIFACT_INDEX.md)

Private career-material drafts are intentionally excluded from the public
repository and from Phase 15 validation/hash scope.

Accepted Phase 14 commit:
`7290d86afa18b67fdf0c46b2eeea22253dab7bc1`. GitHub Actions on that commit:
tests run `31413110254` PASS and research-smoke run `31413111431` PASS.

## Limitations

This is a BTC-USDT research system using Tardis/Binance historical
reconstruction. It uses displayed book data only, does not observe hidden
liquidity, does not model self-impact or market reaction, and uses an
approximate passive queue model. Fee overlays are generic research stresses,
not live venue pricing.

The execution robustness sample is limited relative to the full development
universe. The report synthesizes development evidence through Phase 14 and does
not claim final confirmatory validation. 2026 remains reserved as an untouched
temporal holdout.
