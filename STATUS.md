# Project Status

This file is the source of truth for phase completion, test results,
assumptions, unresolved risks, and next steps. The project specification is
treated as immutable unless the specification itself requires revision.

## Phase Checklist

```text
[x] Phase 0 - Repository foundation
[x] Phase 1 - Raw market-data ingestion
[x] Phase 2 - Market-data QA
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

## Phase 1 Follow-Up / Phase 2 Prerequisites - Real-Data Hardening

Status: PASS with documented L2 download limitation

Data-source policy:

- Canonical internal instrument: `BTC-USDT`.
- Binance Spot vendor symbol: `BTCUSDT`.
- Tardis normalized Binance Spot vendor symbol: `BTCUSDT`.
- Binance official public historical Spot trades are used for real full-day
  trade ingestion validation.
- Binance's standard public historical archive is not assumed to provide
  historical incremental Spot L2 updates.
- Tardis is selected as the L2 replay-path source because Tardis exchange
  metadata confirms Binance Spot `BTCUSDT` supports `incremental_book_L2`.

Timestamp semantics:

- Binance Spot trades on `2024-01-01` use `time` in milliseconds since Unix
  epoch, UTC. Binance documents Spot timestamps as microseconds from
  `2025-01-01` onward, so the adapter chooses units from documented source/date
  semantics, not from the column name alone.
- Tardis normalized `incremental_book_L2` uses `timestamp` and
  `local_timestamp` in microseconds since Unix epoch, UTC. `timestamp` is the
  exchange timestamp where available; `local_timestamp` is message-arrival time.

Real trade-data validation:

- Source: Binance official public archive.
- URL:
  `https://data.binance.vision/data/spot/daily/trades/BTCUSDT/BTCUSDT-trades-2024-01-01.zip`
- Checksum URL:
  `https://data.binance.vision/data/spot/daily/trades/BTCUSDT/BTCUSDT-trades-2024-01-01.zip.CHECKSUM`
- Date: `2024-01-01`.
- Vendor checksum:
  `a312617d895cdae43a58551f05fc9bc6b97285fab2e5d0daf774b4fb61e0a0c0`.
- Project SHA-256:
  `a312617d895cdae43a58551f05fc9bc6b97285fab2e5d0daf774b4fb61e0a0c0`.
- Vendor checksum verification: PASS.
- Full-day rows ingested: `1,114,623`.
- Raw zip preserved under `/tmp/microalpha-real-ingest/raw/...`.
- Bronze file produced under `/tmp/microalpha-real-ingest/bronze/...`.
- Metadata manifest produced under `/tmp/microalpha-real-ingest/manifests/...`.
- No real/raw data files were written under repository `data/`.

Actual Binance source schema encountered:

```text
trade_id, price, quantity, quote_quantity, time, is_buyer_maker, is_best_match
```

Binance normalization mapping:

- `time` -> `event_time`, converted from documented milliseconds to UTC ISO.
- No Binance receive timestamp exists in this archive, so `receive_time` is
  empty.
- `price` -> `price`.
- `quantity` -> `quantity`.
- `trade_id` -> `trade_id` and `source_trade_id`.
- `quote_quantity` preserved.
- `is_buyer_maker=True` -> aggressive `side=sell`; `False` -> `side=buy`.
- `is_best_match` preserved.
- Adds `instrument=BTC-USDT` and `source_checksum`.

Tardis L2 adapter:

- Adapter implemented for Tardis normalized `incremental_book_L2` gzip CSV.
- Expected Tardis source schema:

```text
exchange, symbol, timestamp, local_timestamp, is_snapshot, side, price, amount
```

- Tardis normalization mapping:
  - `timestamp` -> `event_time`, converted from microseconds UTC.
  - `local_timestamp` -> `receive_time`, converted from microseconds UTC.
  - `amount` -> `quantity`.
  - `is_snapshot=true` -> `update_type=snapshot`; otherwise `set`.
  - `side`, `price`, vendor fields, and source timestamps are preserved.
- Direct Tardis sample URLs tested for Binance Spot `BTCUSDT`
  `incremental_book_L2` returned HTTP 404 from this environment, despite Tardis
  metadata listing the dataset support. The adapter is therefore tested with a
  tiny schema-conformant Tardis regression fixture, and the real Tardis sample
  download remains a follow-up before Phase 3 replay work.

Regression fixtures:

- `tests/fixtures/real_subsets/binance_spot_BTCUSDT_trades_2024-01-01_first5.csv`
  contains the first five rows from the real Binance full-day trade file.
- `tests/fixtures/real_subsets/tardis_binance_incremental_l2_schema_sample.csv`
  contains a tiny Tardis normalized incremental L2 schema fixture.

## Phase 2 - Market-Data QA

Status: PASS

QA threshold configuration:

- Config file: `configs/qa.yaml`.
- Timestamp bounds: `2010-01-01T00:00:00+00:00` to
  `2035-01-01T00:00:00+00:00`.
- Extreme price discontinuity ERROR threshold: `1000` bps consecutive observed
  price/BBO-mid move.
- Extreme quantity WARNING threshold: absolute quantity above `1,000,000`.
- Update gap WARNING threshold: `60,000` ms between consecutive event times.
- Stale BBO WARNING threshold: unchanged BBO longer than `300,000` ms.

Severity policy:

- `ERROR`: downstream research must stop; QA report sets `can_continue=false`.
- `WARNING`: processing may continue; the issue remains visible in the QA
  report.

Validators implemented:

- Missing/corrupted/impossible/naive/backward timestamps.
- Exact duplicate rows.
- Missing sequence gaps, repeated sequence IDs, out-of-order sequence IDs, and
  non-integer sequence IDs when present.
- Non-positive prices and unparsable prices.
- Negative quantities and unparsable quantities.
- Crossed books and locked books when BBO columns are present.
- Extreme consecutive price discontinuities.
- Extreme size outliers.
- Inter-update gaps.
- Stale BBO.

QA report schema:

- Deterministic JSON with `status`, `can_continue`, `row_count`,
  `duplicate_count`, `sequence_gap_count`, `crossed_book_count`,
  `locked_book_count`, `timestamp_error_count`, `warning_count`, `error_count`,
  sorted `warnings`, and structured `issues`.

Test results:

- `PYTHONPYCACHEPREFIX=/tmp/microalpha-pycache PYTHONPATH=src python3 -m compileall -q src scripts tests`:
  PASS.
- `/tmp/microalpha-phase0-venv/bin/ruff check src tests scripts`: PASS.
- `/tmp/microalpha-phase0-venv/bin/python -m pytest`: PASS, 31 tests.
- `/tmp/microalpha-phase0-venv/bin/microalpha-qa --input-path tests/fixtures/phase2/clean_book.csv --dataset-type book_updates --report-out /tmp/microalpha-phase2-clean-qa.json`:
  PASS, exit code 0.
- `/tmp/microalpha-phase0-venv/bin/microalpha-qa --input-path tests/fixtures/phase2/zero_price.csv --dataset-type trades --report-out /tmp/microalpha-phase2-fail-qa.json`:
  PASS, exit code 2 with `can_continue=false`.

Real-data QA result:

- Input:
  `/tmp/microalpha-real-ingest/bronze/binance_spot/BTC-USDT/2024-01-01/trades/a312617d895cdae4.csv`
- Dataset type: `trades`.
- Rows: `1,114,623`.
- Status: PASS.
- `can_continue`: true.
- Errors: `0`.
- Warnings: `0`.
- Duplicates: `0`.
- Timestamp errors: `0`.
- QA report:
  `/tmp/microalpha-real-ingest/qa/binance_spot_BTCUSDT_trades_2024-01-01_qa.json`

Acceptance-gate evidence:

- Synthetic corruption fixtures exist for timestamp errors, duplicates,
  sequence gaps/repeats/out-of-order IDs, invalid prices, negative quantities,
  crossed/locked books, extreme price discontinuities, extreme sizes, update
  gaps, stale BBO, and combined corruption.
- Clean fixture passes.
- Deterministic QA report output test passes.
- Real BTC-USDT full-day trade QA smoke passes.
- Invalid critical data prevents downstream continuation.

Known limitations:

- Python 3.11+ CI remains unconfirmed in this workspace.
- Tardis direct sample file download returned HTTP 404 for the tested Binance
  Spot `BTCUSDT` incremental L2 URLs. The adapter and tests are ready for the
  Tardis normalized schema, but a live Tardis incremental L2 sample should be
  downloaded and recorded before Phase 3 order-book replay is trusted.

Next steps:

- Do not begin Phase 3 until the user accepts Phase 2 or requests continuation.
- Before Phase 3 replay, resolve the Tardis sample download path or obtain a
  small real Tardis `incremental_book_L2` BTCUSDT gzip file and run the adapter
  plus QA against it.
