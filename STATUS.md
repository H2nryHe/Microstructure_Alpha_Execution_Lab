# Project Status

This file is the source of truth for phase completion, test results,
assumptions, unresolved risks, and next steps. The project specification is
treated as immutable unless the specification itself requires revision.

## Phase Checklist

```text
[x] Phase 0 - Repository foundation
[x] Phase 1 - Raw market-data ingestion
[ ] Phase 2 - Market-data QA
[ ] Phase 3 - Order-book reconstruction
[ ] Phase 4 - Research dataset construction
[ ] Phase 5 - Feature engineering
[ ] Phase 6 - Label generation
[ ] Phase 7 - Baseline statistical research
[ ] Phase 8 - Predictive modeling
[ ] Phase 9 - Walk-forward evaluation
[ ] Phase 10 - Signal construction
[ ] Phase 11 - Execution simulator
[ ] Phase 12 - Portfolio / inventory accounting
[ ] Phase 13 - Cost and latency analysis
[ ] Phase 14 - Robustness and regime analysis
[ ] Phase 15 - Research report
[ ] Phase 16 - Performance engineering
[ ] Phase 17 - Final packaging
```

## Phase 0 - Repository Foundation

Status: PASS

Test results:

- `pytest`: PASS, 4 tests.
- `ruff check src tests scripts`: PASS.
- Installed CLI smoke command generated a run manifest.
- Package import without `PYTHONPATH`: PASS in a temporary editable install.

Assumptions and risks:

- Local machine Python is 3.9.6, while the project requires Python 3.11+.
- Local verification used a temporary Python 3.9 virtual environment and forced
  editable install past the Python-version gate only to exercise the code paths.
- This is not equivalent to Python 3.11+ compatibility verification.
- Proper Python 3.11+ verification must come from GitHub Actions or a real
  Python 3.11+ environment.
- GitHub Actions has been configured, but no remote CI result has been confirmed
  in this workspace.

## Phase 1 - Raw Market-Data Ingestion

Status: PASS

Data-source decision:

- Initial instrument: `BTC-USDT`.
- MVP source: local CSV files supplied by the user or downloaded outside the
  pipeline from an exchange/vendor source.
- Rationale: file-based ingestion is deterministic, works without live network
  access, keeps raw source files unchanged, and is instrument-agnostic.
- Test data: tiny CSV fixtures committed under `tests/fixtures/phase1`.

Assumptions:

- All input timestamps are source-local strings until bronze normalization.
- The configured source timezone is `UTC`.
- Raw files are copied byte-for-byte into `data/raw` and never modified by the
  ingestion step.
- Bronze output is a normalized CSV artifact for Phase 1. Parquet can be added
  later once the runtime has PyArrow available and downstream needs justify it.

Raw schemas:

- `trades`: required `event_time`, `price`, `quantity`; optional
  `receive_time`, `side`, `trade_id`.
- `book_updates`: required `event_time`, `side`, `price`, `quantity`; optional
  `receive_time`, `update_type`, `sequence_id`.
- `snapshots`: required `event_time`; optional top-N `bid_px_N`, `bid_sz_N`,
  `ask_px_N`, `ask_sz_N` columns are supported for levels 1-10 when present.

Bronze schema:

- Preserves practical source fields from the raw CSV.
- Normalizes `event_time` and optional `receive_time` to UTC ISO-8601 strings.
- Adds `source_event_time` and optional `source_receive_time` before timestamp
  normalization.
- Normalizes price/quantity fields as decimal text, `side` and `update_type` to
  lowercase, and `sequence_id` to integer text when present.
- Adds `instrument` and `source_checksum` lineage columns.

Implementation notes:

- Raw files are copied byte-for-byte into checksum-addressed paths under
  `data/raw` or the configured raw directory.
- SHA-256 is computed before and after copy; mismatches fail loudly.
- Metadata manifests are JSON files under `data/manifests/phase1` or the
  configured manifest directory.
- Re-ingesting the same source checksum reuses the same raw and bronze paths and
  does not create duplicate raw data.

Test results:

- `PYTHONPYCACHEPREFIX=/tmp/microalpha-pycache PYTHONPATH=src python3 -m compileall -q src scripts tests`:
  PASS.
- `/tmp/microalpha-phase0-venv/bin/ruff check src tests scripts`: PASS.
- `/tmp/microalpha-phase0-venv/bin/python -m pytest`: PASS, 11 tests.
- `/tmp/microalpha-phase0-venv/bin/microalpha-ingest --source-path tests/fixtures/phase1/btc_usdt_trades_2026-01-02.csv --dataset-type trades --instrument BTC-USDT --trade-date 2026-01-02 --raw-dir /tmp/microalpha-phase1-installed/raw --bronze-dir /tmp/microalpha-phase1-installed/bronze --manifest-dir /tmp/microalpha-phase1-installed/manifests`:
  PASS.

Acceptance-gate evidence:

- Immutable raw file: PASS; fixture raw bytes match copied raw bytes exactly.
- Checksum: PASS; SHA-256 is stored in the manifest and re-reading the copied raw
  file reproduces the same digest.
- Metadata manifest: PASS; JSON manifest includes source path, checksum,
  ingestion timestamp, timezone, instrument, trade date, row count, required
  columns, raw path, bronze path, and source metadata.
- Normalized bronze output: PASS; bronze CSV is produced separately from raw.
- Schema tests: PASS; missing required columns are rejected.
- Type tests: PASS; timestamps parse, prices must be positive, quantities must
  be non-negative, and sequence IDs must be integral when present.
- Duplicate-ingestion test: PASS; repeated ingestion of the same fixture does
  not duplicate raw or bronze files.
- Large data files: PASS; no non-`.gitkeep` files exist under repository
  `data/`.

Unresolved risks:

- Python 3.11+ CI has not yet been confirmed. There is no configured Git remote
  and no `gh` CLI available in this workspace.
- A real exchange/vendor full-day BTC-USDT source file has not yet been ingested
  locally; Phase 1 acceptance was verified against tiny fixture-day files as
  requested.

Next steps:

- Do not begin Phase 2 until the user accepts Phase 1 or requests continuation.
- When a real source file is available, run the same ingestion command with
  configured `data/raw`, `data/bronze`, and `data/manifests` directories.
