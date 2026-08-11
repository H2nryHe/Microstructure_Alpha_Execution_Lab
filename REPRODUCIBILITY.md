# Reproducibility

This repository is designed so a fresh technical reviewer can validate the code
and public artifacts without private files or large raw datasets.

## Environment

- Python: 3.11 or newer, matching `pyproject.toml`.
- Install: `python -m pip install -e ".[dev]"`.
- Main local checks:

```bash
python -m pytest
ruff check src tests scripts
python -m compileall -q src scripts tests
microalpha-smoke --manifest-out /tmp/microalpha-smoke.yaml
```

The local development machine used for Phase 17 has Python 3.10.9, so package
installation against the declared Python 3.11 floor must be verified by GitHub
Actions or another real Python 3.11+ environment.

## Smoke vs Full Research

The smoke path validates importability, config loading, deterministic config
hashing, CLI wiring, and manifest generation. It does not download historical
market data and does not rerun the full multi-day research pipeline.

Bounded demonstration command:

```bash
PYTHONPATH=src MPLCONFIGDIR=/tmp/microalpha-mpl python3 scripts/run_phase16_performance.py --output-dir /tmp/microalpha-phase16-demo/reports --work-root /tmp/microalpha-phase16-demo/work --repetitions 1
```

The full historical research path uses Binance/Tardis BTC-USDT historical trade
and incremental L2 data. Large raw and derived datasets are intentionally kept
outside Git. Full reruns require downloading source files, retaining immutable
raw bytes, verifying checksums where supplied, and rebuilding the bronze,
replay, research, feature, label, modeling, signal, execution, accounting,
cost, robustness, and performance artifacts.

## Data and Hash Policy

Raw source files are immutable byte streams. The ingestion layer records
project SHA-256 checksums and vendor checksums when available. Downstream
artifacts record config hashes, source identities, and deterministic result
hashes. Runtime measurements, timestamps, absolute local paths, and private
career files are excluded from deterministic release identity hashes.

## CI

GitHub Actions runs `tests` and `research-smoke` on Python 3.11 for every push
and pull request. Wall-clock benchmark thresholds are not CI gates; performance
reports are measured local artifacts.

## Holdout Policy

The 2026 data remain sealed. They are reserved for a future confirmatory
evaluation and are not accessed by the smoke path, bounded demo, or Phase 17
packaging checks.
