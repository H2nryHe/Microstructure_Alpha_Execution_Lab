# Microstructure Alpha & Execution Lab

A reproducible research and execution-simulation platform for testing whether
short-horizon order-flow and limit-order-book signals retain economic value
after realistic trading frictions.

This repository is currently in Phase 0: repository foundation.

## Quick Start

```bash
python -m pip install -e ".[dev]"
pytest
microalpha-smoke
```

## Phase 0 Contents

- installable Python package under `src/microalpha`
- YAML configuration files
- deterministic config hashing
- run manifest generation
- CLI smoke command
- unit and integration tests
- GitHub Actions test and research-smoke workflows

## Architecture Target

```text
Raw Market Data
      |
      v
Data QA
      |
      v
Order Book Replay
      |
      v
Research Dataset
      |
      +--> Features
      |
      +--> Future Labels
      |
      v
Walk-Forward Modeling
      |
      v
Trading Signals
      |
      v
Latency + Execution Simulator
      |
      v
Positions / PnL
      |
      v
Robustness Analysis
      |
      v
Research Report
```
