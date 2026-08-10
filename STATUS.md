# Project Status

This file is the source of truth for phase completion, test results,
assumptions, unresolved risks, and next steps. The project specification is
treated as immutable unless the specification itself requires revision.

## Phase Checklist

```text
[x] Phase 0 - Repository foundation
[x] Phase 1 - Raw market-data ingestion
[x] Phase 2 - Market-data QA
[x] Phase 3 - Order-book reconstruction
[x] Phase 4 - Research dataset construction
[x] Phase 5 - Feature engineering
[x] Phase 6 - Label generation
[x] Phase 7 - Baseline statistical research
[x] Phase 8 - Predictive modeling
[x] Phase 9 - Walk-forward evaluation
[x] Phase 10 - Signal construction
[x] Phase 11 - Execution simulator
[ ] Phase 12 - Portfolio / inventory accounting
[ ] Phase 13 - Cost and latency analysis
[ ] Phase 14 - Robustness and regime analysis
[ ] Phase 15 - Research report
[ ] Phase 16 - Performance engineering
[ ] Phase 17 - Final packaging
```

## CI / Config Hardening

Status: PASS locally and in Python 3.11 GitHub Actions

Change:

- Renamed the YAML model key `models.null` to `models.null_baseline` to avoid
  PyYAML interpreting the reserved unquoted key `null` as Python `None`.
- Added recursive config validation requiring all mapping keys to be strings
  after YAML loading. Non-string keys now fail clearly before config hashing,
  for example:
  `Non-string YAML mapping key detected at model.models: None. Quote or rename reserved YAML keys.`
- Added hashing-path validation so direct config hashing cannot fail later in
  `json.dumps(sort_keys=True)` with mixed key types.
- Added pytest `pythonpath = ["src"]` so `python -m pytest` works from a
  checkout without relying on an editable install.

Config-key scan:

- Searched all YAML files under `configs/` for mapping keys that PyYAML may
  interpret as non-string scalars: `null`, `~`, `true`, `false`, `yes`, `no`,
  `on`, and `off`.
- The only mapping-key issue found was `configs/model.yaml` `models.null`.
- Existing `null` and `true` scalar values remain unchanged because the
  portability risk is mapping keys, not ordinary scalar values.

Test results:

- `python -m pytest`: PASS, `69 passed in 0.42s`.
- `ruff check src tests scripts`: PASS, `All checks passed!`.
- `PYTHONPYCACHEPREFIX=/tmp/microalpha-pycache python -m compileall -q src scripts tests`:
  PASS.
- `PATH=/tmp/microalpha-config-smoke-venv/bin:$PATH microalpha-smoke --manifest-out /tmp/microalpha-smoke.yaml`:
  PASS, config hash
  `0fbc90654bf03c51df2c806dc0765a213d28882b909b22b5cfc34faca61f7483`.
- GitHub Actions `research-smoke` on commit
  `be1f24e6fb4a1e7e8d7eed4bf23db2877662bbe2`: PASS.
- GitHub Actions run:
  `https://github.com/H2nryHe/Microstructure_Alpha_Execution_Lab/actions/runs/31283323407`.
- Latest pushed commit `82eb73214dad05054f6e428cd74d8a5eb586a689`
  also passed GitHub Actions `research-smoke`.
- Latest GitHub Actions run:
  `https://github.com/H2nryHe/Microstructure_Alpha_Execution_Lab/actions/runs/31283395667`.
- Job `smoke`: PASS. Steps `actions/setup-python@v5`, `Install package`,
  and `Run tiny research smoke test` all completed successfully.

Assumptions and risks:

- Local smoke verification used Python with PyYAML but not Python 3.11 because
  no `python3.11` binary is installed in this workspace.
- Python 3.11 smoke compatibility is confirmed by GitHub Actions
  `research-smoke`, whose workflow config uses `python-version: "3.11"`.
- This is a configuration serialization/CI portability bug and does not change
  the Phase 4 causal research dataset acceptance status.

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
- A direct GET request for the documented Binance Spot `BTCUSDT`
  `incremental_book_L2` sample later succeeded during Phase 3 preflight. The
  downloaded file is recorded in the Phase 3 section.

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
- Phase 2 row/schema QA does not replace Phase 3 state-level book validation.
- Sequence validation is source-dependent. It is N/A for Tardis normalized L2
  because the normalized CSV does not supply vendor sequence IDs; replay uses
  capture-order mode based on source row order and `local_timestamp`.
- Crossed/locked-book checks on individual incremental updates are not a
  substitute for reconstructed-state invariants. Phase 3 enforces crossed-book
  validation after complete logical update groups.

Next steps:

- Phase 3 preflight is required before replay work is considered valid.

## Phase 3 - Order-Book Reconstruction

Status: PASS

Real-data preflight source:

- Source: Tardis downloadable CSV datasets API.
- Exchange: `binance`.
- Vendor symbol: `BTCUSDT`.
- Canonical symbol: `BTC-USDT`.
- Data type: `incremental_book_L2`.
- Date: `2019-12-01`.
- Request path:
  `https://datasets.tardis.dev/v1/binance/incremental_book_L2/2019/12/01/BTCUSDT.csv.gz`
- HTTP status: `200`.
- Local raw path:
  `/tmp/microalpha-real-data/tardis_binance_BTCUSDT_incremental_book_L2_2019-12-01.csv.gz`
- Source SHA-256:
  `f7daa040dc33fc7328ff8468b198731fd5add90bc8cef434aab86726268e8a34`.
- Compressed size: `43,947,405` bytes.

Actual Tardis source schema:

```text
exchange, symbol, timestamp, local_timestamp, is_snapshot, side, price, amount
```

Tardis inspection results:

- Decompressed row count: `6,486,542`.
- Timestamp range: `1575158404999000` to `1575244799808000`
  microseconds since Unix epoch, UTC.
- Local timestamp range: `1575158405045139` to `1575244799929296`
  microseconds since Unix epoch, UTC.
- `is_snapshot=true` rows: `2,000`.
- `is_snapshot=false` rows: `6,484,542`.
- Unique sides: `ask`, `bid`.
- Price range: `1336.92` to `51000`.
- Amount range: `0` to `351.854158`.
- Multiple rows share `local_timestamp`: yes.
- Exchange `timestamp` monotonic in source row order: no.
- `local_timestamp` monotonic in source row order: yes.
- Initial snapshot rows: source rows `1` through `2,000`.
- First incremental row after snapshot: source row `2,001`.

First 10 raw Tardis rows:

```text
binance,BTCUSDT,1575158405045139,1575158405045139,true,ask,7541.38,0.085806
binance,BTCUSDT,1575158405045139,1575158405045139,true,ask,7541.39,0.013013
binance,BTCUSDT,1575158405045139,1575158405045139,true,ask,7541.4,4
binance,BTCUSDT,1575158405045139,1575158405045139,true,ask,7541.44,5.045272
binance,BTCUSDT,1575158405045139,1575158405045139,true,ask,7541.45,3.995438
binance,BTCUSDT,1575158405045139,1575158405045139,true,ask,7541.46,1.971371
binance,BTCUSDT,1575158405045139,1575158405045139,true,ask,7541.47,4
binance,BTCUSDT,1575158405045139,1575158405045139,true,ask,7541.48,0.097294
binance,BTCUSDT,1575158405045139,1575158405045139,true,ask,7541.49,0.3
binance,BTCUSDT,1575158405045139,1575158405045139,true,ask,7541.5,0.082835
```

Source ordering semantics:

- Tardis normalized L2 does not contain `sequence_id`; absence of sequence IDs
  is not an error for this source.
- Replay supports two modes:
  - vendor-sequence mode, used when an explicit source sequence exists;
  - capture-order mode, used for Tardis normalized L2.
- Capture-order mode preserves source row order using `source_row_number` and
  groups logical source messages by `receive_time`/Tardis `local_timestamp`.
- Replay validates book invariants after complete logical update groups, not
  halfway through a multi-row source message.
- Pre-snapshot incremental rows are ignored until the first valid snapshot group
  initializes the book.

Real regression fixture:

- Fixture:
  `tests/fixtures/real_subsets/tardis_binance_BTCUSDT_incremental_book_L2_2019-12-01_rows_1_2050.csv`
- Source row range: contiguous rows `1` through `2,050`, including the full
  `2,000`-row initial snapshot plus `50` subsequent incremental rows.
- Fixture SHA-256:
  `bde8b4ce360d2f8e8a226559aa9b69480fdf5b442981614357b1483f485d740a`.
- Sampling method: contiguous source-order extraction; no random sampling.

Implementation notes:

- Ordered price levels use a sorted price list plus quantity map per side.
- Price insertion/removal uses binary search and list insertion/removal; replay
  does not sort the full book on every event.
- Baseline complexity is O(log n) search plus O(n) list shift on price-level
  insert/delete, acceptable for Phase 3 correctness.
- Maintains bid levels, ask levels, quantities, top-N depth, best bid, best ask,
  mid, and spread.

Real-data replay validation:

- Input: first `50,000` contiguous rows of the actual Tardis gzip, normalized
  through the Tardis adapter into `/tmp/microalpha-tardis-preflight/bronze/...`.
- Rows/events processed: `50,000`.
- Initial snapshot location: rows `1` through `2,000`.
- Timestamp range replayed:
  `2019-12-01T00:00:05.045139+00:00` to
  `2019-12-01T00:12:57.774000+00:00`.
- Final best bid: `7506.07`.
- Final best ask: `7506.95`.
- Inserts: `21,739`.
- Updates: `6,260`.
- Deletions: `19,576`.
- No-op deletes: `2,425`.
- Invalid/crossed states: `0`.
- Replay resets: `0`.
- Rows ignored before snapshot: `0`.
- Processing time: approximately `0.435` seconds.
- Deterministic final-state match: true.
- Deterministic output hash match: true.
- Output hash:
  `3d8e5fdde91499db9b68d5f9f73a48698ac370b323eddff439ac3840cf1d19f7`.

Real Tardis row-level QA:

- QA input: same `50,000`-row Tardis bronze sample.
- Ordering timestamp: `receive_time`, matching Tardis `local_timestamp`
  capture-order semantics.
- Status: PASS.
- Errors: `0`.
- Warnings: `0`.
- `can_continue`: true.

Tests:

- Synthetic Phase 3 tests cover deterministic hand-built book state, level
  deletion, best-bid improvement, best-ask improvement, multi-level depth
  ordering, crossed reconstructed state detection, pre-snapshot update handling,
  same-local-timestamp source-order preservation, deterministic replay,
  no-sequence-ID Tardis replay, and vendor-sequence replay.
- Real regression tests cover Tardis snapshot initialization and contiguous
  source-order replay.

Test results:

- `/tmp/microalpha-phase0-venv/bin/python -m pytest`: PASS, 42 tests.
- `/tmp/microalpha-phase0-venv/bin/ruff check src tests scripts`: PASS.
- `PYTHONPYCACHEPREFIX=/tmp/microalpha-pycache PYTHONPATH=src python3 -m compileall -q src scripts tests`:
  PASS.

Acceptance-gate evidence:

- Real Tardis L2 dataset successfully acquired and inspected.
- Real initial snapshot reconstructed.
- Real incremental updates replayed.
- Synthetic and real-data regression tests pass.
- Source ordering semantics are documented.
- No hidden unresolved book-state inconsistency remains; real replay reported
  zero invalid/crossed states.

Next steps:

- Phase 4 is now complete. Stop before Phase 5 until the user accepts Phase 4 or
  requests continuation.

## Phase 4 - Research Dataset Construction

Status: PASS

Scope control:

- Phase 5 feature engineering was not started.
- No queue imbalance, OFI, microprice, labels, models, or trading signals were
  implemented.

Full-day Phase 3 hardening:

- Full-day Tardis L2 input rows: `6,486,542`.
- Initial snapshot rows: `2,000`, with `1,000` ask rows and `1,000` bid rows.
- Effective snapshot-depth limitation: research state is initialized from the
  finite Tardis snapshot depth present in the file, not infinite/full market
  depth. Top-10 state is reliable only within the maintained levels after this
  finite initialization and subsequent updates.
- Timestamp range replayed:
  `2019-12-01T00:00:05.045139+00:00` to
  `2019-12-01T23:59:59.808000+00:00`.
- Inserts: `2,671,052`.
- Updates: `835,070`.
- Deletes: `2,663,854`.
- No-op deletes: `316,566`.
- Crossed/invalid states: `0`.
- Resets: `0`.
- Final best bid: `7390.16`.
- Final best ask: `7391.55`.
- Final top-5 bids:
  `7390.16 x 0.027057`, `7390.13 x 4.09756`, `7390.11 x 0.59991`,
  `7390.09 x 4`, `7390.07 x 0.497399`.
- Final top-5 asks:
  `7391.55 x 0.022018`, `7391.56 x 0.046975`, `7391.61 x 0.05`,
  `7392.99 x 0.09282`, `7393 x 2`.
- Processing time: approximately `68.061` seconds for the first full replay.
- Deterministic output hash:
  `582d22aea26c6f177ba7682cc67f02f81697dd6d0a28bc5b2274ab2476d6d110`.
- Second full replay output hash matched exactly.

No-op delete investigation:

- Tardis represents zero `amount` rows as level removals.
- A no-op delete means the removal references a price level that is not present
  in the maintained book at that point.
- This is not automatically erroneous because the replay starts from a finite
  2,000-row snapshot, not an infinite-depth book; later removals can reference
  levels outside the initialized/maintained depth or levels already removed by a
  prior update.
- No-op deletes do not alter top-N state directly because they remove no
  currently maintained level. They are counted and reported because excessive
  no-ops may indicate snapshot-depth limitations or vendor stream semantics.

Same-day trade data:

- Source: Tardis downloadable CSV datasets API.
- Exchange: `binance`.
- Vendor symbol: `BTCUSDT`.
- Canonical symbol: `BTC-USDT`.
- Data type: `trades`.
- Date: `2019-12-01`.
- Request path:
  `https://datasets.tardis.dev/v1/binance/trades/2019/12/01/BTCUSDT.csv.gz`
- HTTP status: `200`.
- Local raw path:
  `/tmp/microalpha-real-data/tardis_binance_BTCUSDT_trades_2019-12-01.csv.gz`
- Compressed size: `6,669,039` bytes.
- SHA-256:
  `6a6a2bf2cb8a609f8f2ba4b264d6f3bb31dd3c8b39f93644a87f53e83202e258`.
- Row count: `420,562`.
- Headers:
  `exchange, symbol, timestamp, local_timestamp, id, side, price, amount`.
- Exchange timestamp range:
  `1575158403572000` to `1575244799868000`.
- Local timestamp range:
  `1575158403820370` to `1575244799991952`.
- Exchange timestamp monotonic in source order: true.
- Local timestamp monotonic in source order: true.
- Trade side/aggressor semantics: Tardis normalized `side` is `buy` or `sell`.
- Price range: `7210` to `7541.46`.
- Quantity range: `0.000001` to `63.2398`.
- Bronze trade rows normalized: `420,562`.

First Tardis trade rows:

```text
binance,BTCUSDT,1575158403572000,1575158403820370,211646077,sell,7540.78,0.039741
binance,BTCUSDT,1575158403579000,1575158403820405,211646078,sell,7540.78,0.035479
binance,BTCUSDT,1575158403622000,1575158403820575,211646079,buy,7541.46,0.03974
binance,BTCUSDT,1575158403857000,1575158404041373,211646080,buy,7541.45,0.004562
binance,BTCUSDT,1575158404680000,1575158404802170,211646081,sell,7540.78,0.023631
```

Real trade regression fixture:

- Fixture:
  `tests/fixtures/real_subsets/tardis_binance_BTCUSDT_trades_2019-12-01_rows_1_100.csv`
- Source row range: contiguous rows `1` through `100`.
- Fixture SHA-256:
  `cedb2fd4e2f52e10acc1334cd493aa9aae448ff74a9fe3075a94e00a6682a50f`.

Causal time contract:

- `event_time`: exchange-origin timestamp, preserved for analysis.
- `observation_time`: local/receive timestamp at which the event became
  observable.
- `source_row_number`: immutable source-order tie breaker.
- `feature_cutoff_time`: latest observation time information is allowed to use.
- For Tardis research data, causal replay and feature availability are based on
  `observation_time` / `local_timestamp` plus preserved source row ordering.
- The captured stream is not resorted by non-monotonic exchange `event_time`.

Dataset views implemented:

- Event-state dataset: one row per fully completed logical book-state update
  group after the active book has been initialized from a valid snapshot.
- Fixed-clock dataset: 100 ms configurable grid using backward/as-of semantics;
  a grid row at `T` can only use a fully completed state with
  `observation_time <= T`.
- Maximum book-state staleness is configurable; stale rows are marked
  unavailable rather than carried indefinitely.
- Trade alignment uses only trades with `trade_observation_time <= T`; exchange
  trade timestamps are preserved separately.
- Top-10 book depth is emitted.

Required research-table fields:

- Event/fixed rows include `instrument`, `observation_time`,
  `feature_cutoff_time`, `book_event_time`, `book_observation_time`,
  `book_source_row_number`, best bid/ask, top-10 bid/ask prices and sizes,
  `mid`, `spread`, and latest eligible trade event/observation fields for
  fixed-clock rows.
- No forward-return labels are present.

Storage:

- PyArrow is not installed in the current local test environment, so verified
  large real-data outputs are deterministic CSV files under `/tmp`.
- Parquet remains preferred once a proper PyArrow runtime is available; testing
  was not weakened because PyArrow is absent locally.

Meaningful real interval validation:

- L2 source rows: `50,000`.
- Trade source rows: `420,562`.
- Event-state rows: `6,983`.
- Fixed-clock rows: `7,729`.
- Unavailable/stale rows: `0`.
- Sampling interval: `100` ms.
- Timestamp range:
  `2019-12-01T00:00:05.045139+00:00` to
  `2019-12-01T00:12:57.845139+00:00`.
- Duplicate research timestamps: `0`.
- Crossed/invalid states: `0`.
- Max RSS reported by process: `557,318,144` bytes-equivalent on macOS.
- Event-state processing time: approximately `0.663` seconds.
- Fixed-clock processing time: approximately `2.538` seconds.
- Event-state hash:
  `6b9e0a4d3bcdfa8a3ecef5a4af37abce0e878c861a0eb6ef933b3ebc16f28bfb`.
- Fixed-clock hash:
  `3de878a37c24ccd3af29e57bb832d6db898d08db98139c64f405f82c3f49e099`.
- Independent rerun on the same interval produced identical hashes.

Full-day Phase 4 validation:

- L2 source rows: `6,486,542`.
- Trade source rows: `420,562`.
- Event-state rows: `815,980`.
- Fixed-clock rows: `863,949`.
- Unavailable/stale rows: `13`.
- Sampling interval: `100` ms.
- Timestamp range:
  `2019-12-01T00:00:05.045139+00:00` to
  `2019-12-01T23:59:59.845139+00:00`.
- Duplicate research timestamps: `0`.
- Crossed/invalid states: `0`.
- Max RSS reported by process: `1,527,881,728` bytes-equivalent on macOS.
- Event-state processing time: approximately `84.819` seconds.
- Fixed-clock processing time: approximately `71.381` seconds.
- Event-state output:
  `/tmp/microalpha-phase4-full-day/event_states_full.csv`.
- Fixed-clock output:
  `/tmp/microalpha-phase4-full-day/fixed_100ms_full.csv`.
- Event-state hash:
  `8953bdab6d46556d8f1b51a18695e00050b53abc9d87ad52090e12bb441f876e`.
- Fixed-clock hash:
  `46f7fedf461bdaad807c7a16c96bd2a3f543c48c45e1be6097173853a16c16e9`.

100 ms grid row-count clarification:

- The full-day fixed-clock table contains `863,949` rows rather than the
  calendar-day maximum of `864,000` because the grid is bounded by observable
  book state availability, not midnight-to-midnight wall-clock time.
- Grid start is the first completed, valid book-state observation:
  `2019-12-01T00:00:05.045139+00:00`.
- Grid end is the last grid timestamp `<=` the final completed book-state
  observation:
  `2019-12-01T23:59:59.929296+00:00`; with 100 ms spacing from the start, the
  final emitted grid cutoff is `2019-12-01T23:59:59.845139+00:00`.
- Row count is therefore:
  `floor((2019-12-01T23:59:59.929296 - 2019-12-01T00:00:05.045139) / 100ms) + 1
  = 863,949`.

Logical book update group clarification:

- A complete logical book update group is identified by the source adapter's
  documented ordering/grouping semantics.
- For Tardis normalized L2, rows are preserved in source order using
  `source_row_number`, and rows sharing the same `local_timestamp`
  (`receive_time`) are treated as one completed observable group because Tardis
  emits normalized book updates with `local_timestamp` as the capture/arrival
  timestamp for that source message.
- Equal timestamps alone are not treated as a universal proof of atomicity for
  other vendors. If a source supplies explicit sequence/message IDs, the replay
  layer must use those identifiers instead.
- Source ordering is preserved in all modes; event-state rows are emitted only
  after the full applicable group has been processed and validated.

Manual fixed-clock row audits:

```text
cutoff=2019-12-01T00:00:05.045139+00:00
selected_book=2019-12-01T00:00:05.045139+00:00 source_row=2000
latest_trade=2019-12-01T00:00:04.802441+00:00
next_book=2019-12-01T00:00:05.116597+00:00 next_trade=2019-12-01T00:00:05.701053+00:00

cutoff=2019-12-01T06:00:03.745139+00:00
selected_book=2019-12-01T06:00:03.690874+00:00 source_row=1849626
latest_trade=2019-12-01T06:00:03.496320+00:00
next_book=2019-12-01T06:00:03.792105+00:00 next_trade=2019-12-01T06:00:03.902115+00:00

cutoff=2019-12-01T12:00:02.445139+00:00
selected_book=2019-12-01T12:00:02.357883+00:00 source_row=3395608
latest_trade=2019-12-01T12:00:02.314170+00:00
next_book=2019-12-01T12:00:02.458453+00:00 next_trade=2019-12-01T12:00:02.785756+00:00

cutoff=2019-12-01T18:00:01.145139+00:00
selected_book=2019-12-01T18:00:01.080147+00:00 source_row=5062419
latest_trade=2019-12-01T18:00:00.327022+00:00
next_book=2019-12-01T18:00:01.180721+00:00 next_trade=2019-12-01T18:00:02.357109+00:00

cutoff=2019-12-01T23:59:59.845139+00:00
selected_book=2019-12-01T23:59:59.829502+00:00 source_row=6486535
latest_trade=2019-12-01T23:59:58.785719+00:00
next_book=2019-12-01T23:59:59.929296+00:00 next_trade=2019-12-01T23:59:59.991952+00:00
```

All audited selected book/trade observations are `<= cutoff`; the listed next
book/trade records are strictly after cutoff and were not selected.

Tests:

- Temporal causality test: PASS.
- Future mutation test: PASS.
- Sampling boundary test: PASS.
- Exact boundary test: PASS.
- Same-local-timestamp ordering/grouping test: PASS.
- Staleness test: PASS.
- Snapshot boundary test: PASS.
- Determinism/hash test: PASS.
- No-event-time-resort test: PASS.
- Trade alignment leakage test: PASS.
- Real contiguous Phase 4 regression fixture test: PASS.

Test results:

- `/tmp/microalpha-phase0-venv/bin/python -m pytest`: PASS, 51 tests.
- `/tmp/microalpha-phase0-venv/bin/ruff check src tests scripts`: PASS.
- `PYTHONPYCACHEPREFIX=/tmp/microalpha-pycache PYTHONPATH=src python3 -m compileall -q src scripts tests`:
  PASS.

Acceptance-gate evidence:

- Full-day L2 replay is trusted and deterministic.
- Same-day real Tardis trades were acquired and normalized.
- Causal observation-time semantics are explicit in code and status.
- Source ordering is preserved via `source_row_number`.
- Event-state research table works.
- Fixed-clock research table works.
- Backward/as-of sampling cannot select future book state.
- Trade alignment cannot select future trades.
- Future-mutation leakage tests pass.
- Deterministic dataset hash is demonstrated on real contiguous data.
- Real-data row audits show no future information usage.

Known limitations:

- Python 3.11+ CI remains unconfirmed in this workspace.
- Parquet output is deferred until PyArrow is available in the runtime.
- The initial Tardis snapshot is finite depth, so top-10 research state is
  supported, but no claim is made about complete full-depth book history.

Next steps:

- Phase 5 feature engineering is now complete. Stop before Phase 6 until the
  user accepts Phase 5 or requests continuation.

## Phase 5 - Feature Engineering

Status: PASS

Files created or modified:

- `STATUS.md`
- `configs/features.yaml`
- `src/microalpha/features/__init__.py`
- `src/microalpha/features/engineering.py`
- `src/microalpha/features/metadata.py`
- `tests/unit/test_phase5_features.py`

Feature version:

- `microstructure_v1`

Feature architecture:

- State features are computed from the latest causally available completed book
  state at `feature_cutoff_time`.
- Flow features are computed from underlying book/trade event streams first and
  then causally aggregated into fixed-clock rows.
- All trailing windows use `(T-W, T]`: events exactly at `T` are included,
  events exactly at `T-W` are excluded, and events after `T` are excluded.
- Tardis observation/local timestamp is the causal eligibility timestamp.

Exact feature definitions:

- `mid = (best_bid + best_ask) / 2`.
- `spread = best_ask - best_bid`.
- `relative_spread = spread / mid`.
- `spread_bps = 10000 * spread / mid`.
- `qi_1 = (bid_sz_1 - ask_sz_1) / (bid_sz_1 + ask_sz_1)`.
- `bid_depth_N = sum(bid_sz_1 ... bid_sz_N)` for `N in {5, 10}`.
- `ask_depth_N = sum(ask_sz_1 ... ask_sz_N)` for `N in {5, 10}`.
- `di_N = (bid_depth_N - ask_depth_N) / (bid_depth_N + ask_depth_N)` for
  `N in {5, 10}`.
- `microprice = ask_px_1 * bid_sz_1 / (bid_sz_1 + ask_sz_1) + bid_px_1 *
  ask_sz_1 / (bid_sz_1 + ask_sz_1)`.
- `microprice_deviation = (microprice - mid) / mid`.
- `microprice_deviation_bps = 10000 * (microprice - mid) / mid`.
- `ofi_event` follows the documented Cont-style BBO transition formula from
  consecutive completed observable BBO states.
- `ofi_W = sum(ofi_event)` over completed BBO transitions in `(T-W, T]`.
- `book_update_count_W = count(completed BBO transitions)` in `(T-W, T]`.
- `buy_volume_W`, `sell_volume_W`, `trade_count_W`, and `trade_notional_W` use
  trades in `(T-W, T]`.
- `trade_imbalance_W = (buy_volume_W - sell_volume_W) / (buy_volume_W +
  sell_volume_W)`.
- `signed_trade_volume_W` is positive for aggressive buys and negative for
  aggressive sells.
- `realized_vol_W = sqrt(sum(log(mid_n / mid_{n-1})^2))` over trailing
  completed-state mid returns in `(T-W, T]`; it is not annualized.
- `mom_W = log(mid_asof_T / mid_asof_(T-W))` using backward/as-of mid
  selection.

Configured windows:

- OFI and book-update count: `100ms`, `500ms`, `1s`, `5s`, `30s`.
- Trade-flow features: `100ms`, `500ms`, `1s`, `5s`, `30s`.
- Realized volatility: `1s`, `5s`, `30s`.
- Momentum: `100ms`, `500ms`, `1s`, `5s`.

Missing-data behavior:

- Missing or stale Phase 4 book state leaves state-dependent features blank.
- Zero denominators for `qi_1`, `di_N`, and microprice-family features produce
  `NaN`.
- Missing depth levels are omitted from depth sums; they are not imputed.
- No-trade windows produce zero count/volume/notional and `NaN`
  `trade_imbalance`.
- Realized volatility and momentum are blank until enough trailing mid history
  exists.

Real data used:

- Canonical instrument: `BTC-USDT`.
- Vendor: Tardis normalized Binance Spot.
- Vendor symbol: `BTCUSDT`.
- Date: `2019-12-01`.
- Event-state input:
  `/tmp/microalpha-phase4-full-day/event_states_full.csv`.
- Fixed-clock input:
  `/tmp/microalpha-phase4-full-day/fixed_100ms_full.csv`.
- Trade input:
  `/tmp/microalpha-tardis-trades/bronze/tardis_binance_spot/BTC-USDT/2019-12-01/trades/6a6a2bf2cb8a609f.csv`.
- Feature output:
  `/tmp/microalpha-phase5-full-day/features_microstructure_v1_full.csv`.
- Summary output:
  `/tmp/microalpha-phase5-full-day/summary.json`.
- Manual audit output:
  `/tmp/microalpha-phase5-full-day/manual_audits.json`.

Full-day real-data results:

- Total feature rows: `863,949`.
- Feature columns: `91`.
- Feature output hash:
  `e95c6dfa6bcb5c21272a267d5f2f3760a3b1f2f53f2af6f3770bee3723419dd2`.
- Full end-to-end feature build processing time on rerun:
  approximately `549.627` seconds.
- Future performance-engineering target: reduce the full-day Phase 5 build
  runtime from approximately `549.627` seconds. Do not optimize this before the
  performance-engineering phase.
- Max RSS during full rerun: `1,502,711,808` bytes-equivalent on macOS.
- The initial full-day feature build wrote the complete CSV but was interrupted
  during the old non-streaming summary pass. The completed CSV was retained,
  row-counted, hashed, summarized with the streaming summary implementation,
  and manually audited.
- A full rerun with the patched streaming summary wrote
  `/tmp/microalpha-phase5-full-day-rerun/features_microstructure_v1_full.csv`
  and reproduced the same feature output hash exactly.

Feature distribution summary:

```text
feature                  missing_rate        min           p1          p5       median          p95         p99         max        mean        std
qi_1                     0.0000150472  -0.999999768  -0.999969654  -0.986454  -0.0144416   0.984090   0.999943   1.000000  -0.0296896  0.705152
di_5                     0.0000150472  -0.998830781  -0.949912445  -0.881049  -0.0755150   0.854105   0.935727   0.998102  -0.0488373  0.569611
di_10                    0.0000150472  -0.996997093  -0.887659958  -0.780111  -0.0721688   0.748735   0.871000   0.992299  -0.0472432  0.487867
spread_bps               0.0000150472   0.0132640     0.0135685     0.0672973  1.76922     4.25807    5.62211   22.3764    1.95446    1.22050
microprice_deviation_bps 0.0000150472 -10.1238150    -2.01402      -1.29917   -0.00284128  1.24599    1.96588    8.40500  -0.0267559  0.783333
ofi_100ms                0.0000000000 -84.315575     -2.69634      -0.800014   0.00000     0.548697   2.50000  108.326    -0.0133520  0.983538
ofi_1s                   0.0000000000 -109.979372   -11.2528       -5.80600    0.00000     5.15535   10.5752   136.908    -0.133520   3.91295
ofi_5s                   0.0000000000 -125.432520   -30.2532      -16.6057    -0.266469   14.5705    26.6764   145.357    -0.667536  10.5192
trade_imbalance_1s       0.135388779   -1.000000     -1.00000      -1.00000    0.380039    1.00000    1.00000    1.00000   0.130085   0.851310
trade_count_1s           0.0000000000   0.000000      0.00000       0.00000    3.00000    16.0000    32.0000  1304.000    4.86784    9.51138
trade_volume_1s          0.0000000000   0.000000      0.00000       0.00000    0.190464    2.56925    9.37140   267.339    0.703365   2.91425
realized_vol_5s          0.0000011575   0.000000      0.00000240    0.00000478 0.0001169   0.0003886  0.000643  0.004571  0.0001467  0.0001569
mom_1s                   0.0000115748  -0.00485147   -0.0003059    -0.0001481  0.000000    0.0001497  0.000319  0.003057 -0.000000232 0.0001001
```

Manual real-data audits:

- Five deterministic rows were audited at feature row indexes `10006`,
  `200000`, `400000`, `600000`, and `800000`.
- Each audit records `feature_cutoff_time`, `book_source_row_number`, top-of-book
  source inputs, OFI source-event index range, trade source-row index range, and
  as-of mids.
- Recomputed `qi_1`, `microprice`, `ofi_1s`, `trade_imbalance_1s`, and `mom_1s`
  matched feature output exactly or within `1e-18` float tolerance for
  momentum.
- Audit examples:
  - row `10006`, cutoff `2019-12-01T00:16:45.645139+00:00`,
    book source row `73989`: `qi_1=0.9737111542805600800142501945`,
    `ofi_1s=2.600000`, `trade_imbalance_1s=-1`,
    `mom_1s=1.3321024705939499e-06`.
  - row `400000`, cutoff `2019-12-01T11:06:45.045139+00:00`,
    book source row `3165784`: `qi_1=0.9582831829249456148042292063`,
    `ofi_1s=6.079340`, `trade_imbalance_1s=1`,
    `mom_1s=4.3477876900748396e-05`.
  - row `800000`, cutoff `2019-12-01T22:13:25.045139+00:00`,
    book source row `6021333`: `qi_1=0.7086629303442754203362690152`,
    `ofi_1s=0.106706`, `trade_imbalance_1s=-1`,
    `mom_1s=2.0392383443867545e-06`.

Tests added:

- Exact formula tests for queue imbalance, depth imbalance, microprice,
  microprice deviation, trade imbalance, momentum, and realized volatility.
- Exact numeric tests for all eight OFI BBO transition cases.
- Range tests for imbalance features, spread, depth, and finite numeric output.
- Mirror-symmetry tests for queue imbalance, depth imbalance, microprice
  deviation, and OFI.
- Explicit `(T-W, T]` boundary test.
- Trade-arrival leakage test using receive time.
- Future-mutation leakage test covering state, event-flow, trade-flow,
  realized-volatility, and momentum features.
- Event-stream OFI test proving intermediate BBO events within a 100 ms bin are
  retained.
- Same-observation-time source-order test.
- No-trade-window, stale-row propagation, missing-depth, zero-denominator, and
  deterministic feature-hash tests.

Exact test results:

- `/tmp/microalpha-phase0-venv/bin/python -m pytest`: PASS, `63 passed in
  0.48s`.
- `/tmp/microalpha-phase0-venv/bin/ruff check src tests scripts`: PASS,
  `All checks passed!`.
- `PYTHONPYCACHEPREFIX=/tmp/microalpha-pycache PYTHONPATH=src python3 -m compileall -q src scripts tests`:
  PASS.

Acceptance-gate evidence:

- All required baseline state, flow, activity, realized-volatility, and momentum
  features are implemented.
- Event-level OFI is computed from completed real book-state transitions and
  aggregated causally.
- Real same-day Tardis `BTCUSDT` trades from `2019-12-01` are used for
  trade-flow features.
- All trailing windows use the documented `(T-W, T]` convention.
- Missing/stale behavior is explicit.
- Formula, OFI transition, mirror-symmetry, leakage, event-stream OFI,
  deterministic hash, and source-order tests pass.
- Real-data distributions were inspected for required important features.
- At least five manual real-data audits confirmed selected feature
  calculations.

Assumptions:

- Tardis normalized trade `side` is interpreted as the aggressive trade side;
  buy is positive and sell is negative for signed volume.
- Tardis `local_timestamp` / normalized `receive_time` is the causal eligibility
  clock for both L2 and trade events.
- For same-observation-time L2 groups, source row order remains the tie breaker.
- Missing depth levels represent absent captured levels in the Phase 4 row and
  are omitted from top-N depth sums.

Known limitations:

- Python 3.11+ CI remains unconfirmed in this workspace; local checks still run
  under Python 3.9.6.
- Parquet/PyArrow was not introduced because PyArrow is unavailable locally and
  storage optimization must not block feature correctness.
- The full-day feature artifact is a large CSV under `/tmp` and is not committed
  to Git.
- Feature generation is correct but not yet optimized for production-scale
  memory or storage throughput.
- No labels, IC calculations, models, optimized signals, backtests, or trading
  simulation were implemented.

Next steps:

- Phase 6 label generation was requested after Phase 5 acceptance; see below.

## Phase 6 - Label Generation

Status: PASS locally + PASS in Python 3.11 CI

Files created or modified:

- `STATUS.md`
- `configs/labels.yaml`
- `src/microalpha/labels/__init__.py`
- `src/microalpha/labels/generation.py`
- `src/microalpha/labels/metadata.py`
- `tests/unit/test_phase6_labels.py`

Documentation follow-up before implementation:

- Verified that the Phase 5 feature distribution table already records actual
  missing rate plus min/p1/p5/median/p95/p99/max/mean/std for `qi_1`, `di_5`,
  `di_10`, `spread_bps`, `microprice_deviation_bps`, `ofi_1s`,
  `trade_imbalance_1s`, `realized_vol_5s`, and `mom_1s`; no duplicate table was
  added.
- Recorded the full-day Phase 5 runtime, approximately `549.627` seconds, as a
  future Phase 16 performance-engineering target.

Label version and configuration:

- Label version: `microstructure_labels_v1`.
- Horizons: `100ms`, `500ms`, `1s`, `5s`, `30s`.
- Regression labels: `ret_fwd_100ms`, `ret_fwd_500ms`, `ret_fwd_1s`,
  `ret_fwd_5s`, `ret_fwd_30s`.
- Regression definition: `log(mid_future / mid_T)`.
- Classification labels: `direction_100ms`, `direction_500ms`,
  `direction_1s`, `direction_5s`, `direction_30s`.
- Classification threshold: `0.5` bps, fixed in config and not tuned.
- Classification rule: `UP` if return `> threshold`, `DOWN` if return
  `< -threshold`, otherwise `FLAT`.
- Future lookup rule: `first_observation_at_or_after_horizon`.
- Initial maximum label delay: `100` ms. This matches the Phase 4 fixed-clock
  grid spacing: exact target rows are preferred, the next valid fixed-clock row
  may be accepted, and multi-row/multi-second drift is rejected.
- Default multi-day policy: `cross_session_labels=false`; labels cannot cross
  UTC session/date boundaries by default.
- Binance Spot trades continuously 24/7, so `cross_session_labels=false` refers
  to the project's UTC research-day / dataset-partition boundary, not to an
  exchange market close.
- Next observable mid-change diagnostic search horizon: `30,000` ms.

Timestamp and causality contract:

- Prediction time `T` is `feature_cutoff_time`.
- Target time is `feature_cutoff_time + horizon`.
- Future observations are selected by fixed-clock observation/cutoff time, never
  by exchange `book_event_time`.
- The input table is validated as monotonic by `feature_cutoff_time` in source
  row order; the label generator does not reorder rows by exchange event time.
- Lineage fields are emitted for each horizon:
  `target_time_*`, `actual_label_time_*`, and `label_delay_ms_*`.

Label output schema:

- Base fields: `label_version`, `instrument`, `observation_time`,
  `feature_cutoff_time`, `is_available`, `mid`, `spread`,
  `book_observation_time`, `book_event_time`, `book_source_row_number`.
- Per-horizon fields: `target_time_*`, `actual_label_time_*`,
  `label_delay_ms_*`, `ret_fwd_*`, `direction_*`,
  `future_mid_move_bps_*`, `future_move_in_spreads_*`.
- Next-mid-change diagnostics: `next_mid_change_available`,
  `next_mid_change_direction`, `time_to_next_mid_change_ms`.

Missing-label behavior:

- Current stale/unavailable rows receive missing forward labels.
- Future stale/unavailable rows are skipped; the first valid future row at or
  after the target is accepted only if delay is within `max_label_delay_ms`.
- Missing labels are produced when no valid future state exists, accepted delay
  would exceed the configured limit, the future target crosses the session
  boundary, or the row is at end-of-data.
- Rows are preserved even when some or all horizons are missing.
- `future_move_in_spreads_*` is missing when the current spread is missing or
  non-positive; it uses the current spread, not the future spread.
- `next_mid_change_direction` is only `-1` or `+1` when
  `next_mid_change_available=true`; unavailable/no-observed-move outcomes are
  blank and cannot be interpreted as neutral, down, or up.

Real data used:

- Canonical instrument: `BTC-USDT`.
- Vendor: Tardis normalized Binance Spot.
- Vendor symbol: `BTCUSDT`.
- Date: `2019-12-01`.
- Input fixed-clock research table:
  `/tmp/microalpha-phase4-full-day/fixed_100ms_full.csv`.
- Label output:
  `/tmp/microalpha-phase6-full-day/labels_microstructure_labels_v1_full.csv`.
- Summary output:
  `/tmp/microalpha-phase6-full-day/summary.json`.
- Manual audit output:
  `/tmp/microalpha-phase6-full-day/manual_audits.json`.
- Full-day label rows: `863,949`.
- Label columns: `38`.
- Label output hash:
  `ab39e25fff543b6cf85c62b5266423ab8deba4e4e54edaca1534e0c87712edf9`.
- Full-day label processing time: approximately `140.505` seconds.

Full-day real-data label results:

```text
horizon total_rows valid_reg missing_reg missing_% delay_median delay_p95 delay_max UP_count UP_% FLAT_count FLAT_% DOWN_count DOWN_% return_min return_p1 return_p5 return_median return_p95 return_p99 return_max return_mean return_std
100ms  863949     863932    17          0.001968  0.0          0.0       100.0     19775    2.28895 824394     95.42348 19763      2.28756 -0.00399630 -0.0000983869 -0.00000888332 0.0 0.00000881197 0.0000989676 0.00256402 -0.0000000232 0.0000303320
500ms  863949     863926    23          0.002662  0.0          0.0       100.0     72505    8.39250 717535     83.05515 73886      8.55235 -0.00467907 -0.000210647  -0.0000907562 0.0 0.0000908897  0.000214520  0.00258091 -0.0000001159 0.0000669204
1s     863949     863921    28          0.003241  0.0          0.0       100.0     119800   13.8670 619704     71.73156 124417     14.4014 -0.00485147 -0.000305907  -0.000148145  0.0 0.000149655   0.000318994  0.00305745 -0.0000002319 0.000100079
5s     863949     863881    68          0.007871  0.0          0.0       100.0     276561   32.0138 295967     34.26016 291353     33.7261 -0.00542819 -0.000720928  -0.000414735 -0.000000687545 0.000430237 0.000751737 0.00355407 -0.0000011552 0.000260738
30s    863949     863631    318         0.036808  0.0          0.0       100.0     382429   44.2815 72069      8.34488  409133     47.3736 -0.00613216 -0.00165903   -0.00101031  -0.0000164931 0.00105359 0.00167102 0.00567891 -0.0000067571 0.000653610
```

The table above is descriptive only and has no predictive interpretation.

Manual real-data audits:

- Five cutoffs were audited manually against source fixed-clock rows.
- Exact-target case row `0`, cutoff
  `2019-12-01T00:00:05.045139+00:00`, `mid_T=7540.395`:
  `100ms` exact target return `0.0`, `1s` return
  `-8.620275218937428e-06`; manual and generated values match.
- After-target case row `185605`, cutoff
  `2019-12-01T05:09:25.545139+00:00`, `mid_T=7326.545`:
  `30s` target used actual observation
  `2019-12-01T05:09:55.645139+00:00` with `100` ms delay, return
  `-5.937490511153481e-05`; `1s` exact target return
  `-1.7061393377150398e-05`; manual and generated values match.
- Deterministic row `10006`, cutoff
  `2019-12-01T00:16:45.645139+00:00`, `mid_T=7506.935`:
  `100ms` return `0.0`, `5s` return `6.726886736064051e-05`; manual and
  generated values match.
- Deterministic row `200000`, cutoff
  `2019-12-01T05:33:25.045139+00:00`, `mid_T=7280.11`:
  `100ms` return `0.0`, `5s` return `-0.0003441474193888563`; manual and
  generated values match.
- End-of-data missing case row `863947`, cutoff
  `2019-12-01T23:59:59.745139+00:00`, `mid_T=7390.855`:
  `100ms` exact target return `0.0`; `30s` target
  `2019-12-02T00:00:29.745139+00:00` is missing because labels do not cross the
  session boundary and no valid same-session future exists.

Tests added:

- Exact forward return and exact target-time lookup.
- First-observation-after-target lookup, proving no before-target selection.
- Delay tolerance rejection and end-of-data missing labels.
- Classification boundary behavior.
- Feature-label isolation plus future-mutation asymmetry.
- Monotonic label time, no event-time resort, and unavailable future skip.
- Invalid/stale current-row missing labels.
- Explicit next-mid-change unavailable semantics proving no valid observed move
  cannot be interpreted as `-1`, `0`, or `+1`.
- Valid next-mid-change availability and direction.
- Deterministic label output and deterministic summary output.
- `cross_session_labels=false` session-boundary behavior.

Exact test results:

- `python -m pytest`: PASS, `80 passed in 0.53s`.
- `ruff check src tests scripts`: PASS, `All checks passed!`.
- `PYTHONPYCACHEPREFIX=/tmp/microalpha-pycache python -m compileall -q src scripts tests`:
  PASS.
- `PATH=/tmp/microalpha-config-smoke-venv/bin:$PATH microalpha-smoke --manifest-out /tmp/microalpha-smoke.yaml`:
  PASS, config hash
  `8199bdda9ceea7571824b87d0fcd1927d457efb258075075a853f9dfb8885bd0`.

GitHub Actions state before marking Phase 6:

- Phase 6 commit SHA:
  `a542022b1afb9c0e4766067d9eeb0da7cda9fc39`
  (`Complete Phase 6 label generation`).
- CI Python version: workflows use `actions/setup-python@v5` with
  `python-version: "3.11"`.
- GitHub Actions `tests` on the Phase 6 commit: PASS.
  Run: `https://github.com/H2nryHe/Microstructure_Alpha_Execution_Lab/actions/runs/31284658549`.
  Job `pytest` and step `Run tests` passed.
- GitHub Actions `research-smoke` on the Phase 6 commit: PASS.
  Run: `https://github.com/H2nryHe/Microstructure_Alpha_Execution_Lab/actions/runs/31284658533`.
  Job `smoke` and steps `Install package` and `Run tiny research smoke test`
  passed.
- Local Phase 6 verification used Python `3.10.9`; Python 3.11 compatibility is
  confirmed by the GitHub Actions runs above.

Acceptance-gate evidence:

- Required regression, classification, spread-normalized, bps-move, lineage, and
  next-mid-change diagnostic labels are implemented.
- Labels are produced separately from features.
- Feature code does not import `microalpha.labels` or reference label columns.
- Feature generation is unchanged by future label mutations in regression tests,
  while labels change as expected.
- Future lookup uses `feature_cutoff_time`, not exchange `book_event_time`.
- Invalid/stale current and future states do not create labels.
- Rows are preserved when horizons are missing.
- Multi-day readiness is explicit through `cross_session_labels=false`.
- Full-day real-data smoke and manual audits pass.
- No feature/label correlation, IC, bucket study, predictive evaluation, model,
  signal, threshold optimization, backtest, execution logic, or Phase 7 work was
  implemented.

Assumptions:

- The fixed-clock Phase 4 table is the canonical label input for Phase 6 because
  it already represents causal observation-time research states.
- UTC calendar date is the initial session boundary for `cross_session_labels`.
- A `100` ms maximum label delay is appropriate for the current `100` ms
  fixed-clock grid; this is a configured engineering tolerance, not a tuned
  predictive threshold.

Known limitations:

- Local verification did not run under Python 3.11 because no Python 3.11 binary
  is installed locally; Python 3.11 verification came from GitHub Actions.
- Label output is CSV under `/tmp`; Parquet remains deferred until a proper
  PyArrow runtime is used.
- Label generation is correct but not optimized for memory or throughput.

Next steps:

- Stop before Phase 7 until the user accepts Phase 6 or requests continuation.

## Pre-Phase-7 Multi-Day Data Expansion Gate

Status: SUPERSEDED by the corrected GET/metadata source verification recorded
below. Phase 7 has not started.

Phase 6 CI completion:

- Phase 6 commit SHA:
  `a542022b1afb9c0e4766067d9eeb0da7cda9fc39`
  (`Complete Phase 6 label generation`).
- CI Python version: GitHub Actions workflows use `actions/setup-python@v5` with
  `python-version: "3.11"`.
- `tests` workflow on the Phase 6 commit: PASS.
  Run: `https://github.com/H2nryHe/Microstructure_Alpha_Execution_Lab/actions/runs/31284658549`.
- `research-smoke` workflow on the Phase 6 commit: PASS.
  Run: `https://github.com/H2nryHe/Microstructure_Alpha_Execution_Lab/actions/runs/31284658533`.
- Phase 6 status is therefore: PASS locally + PASS in Python 3.11 CI.

Semantic hardening before data expansion:

- `next_mid_change_direction` no longer uses numeric `0` for unavailable
  outcomes.
- `next_mid_change_available` is emitted explicitly.
- When no valid future mid-price change is observed within the allowed search
  horizon, `next_mid_change_available=false`,
  `next_mid_change_direction=""`, and `time_to_next_mid_change_ms=""`.
- Valid next-mid-price moves remain direction `-1` for down and `1` for up.
- Regression tests prove unavailable next-move outcomes cannot be interpreted as
  neutral, down, or up observations.
- Binance Spot trades 24/7. Therefore `cross_session_labels=false` refers to
  the project's UTC research-day / dataset-partition boundary, not an exchange
  market close.

Frozen date registry:

- Machine-readable registry:
  `data/manifests/research_dates.yaml`.
- Canonical instrument: `BTC-USDT`.
- Vendor: `tardis_binance_spot`.
- Vendor symbol mapping: `BTC-USDT` -> Binance Spot `BTCUSDT`.
- L2 source type: Tardis Binance Spot `incremental_book_L2`.
- Trade source type: Tardis Binance Spot `trades`.
- Development dates: mechanically selected first-of-month dates from
  `2024-01-01` through `2025-12-01`, inclusive, 24 dates.
- Holdout dates: available first-of-month 2026 dates through current local date,
  `2026-01-01` through `2026-08-01`, 8 dates, role `holdout`.
- Engineering/regression validation date remains `2019-12-01` and is not mixed
  into the 2024-2025 research sample.
- The registry records `alpha_analysis_performed_before_freeze=false`.

Legacy HEAD-only source availability result (superseded):

- Development registry source check:
  `PYTHONPATH=src python scripts/check_research_sources.py --registry data/manifests/research_dates.yaml --role development --timeout-seconds 6 --max-workers 12`
  checked 24 dates.
- Development dates with both same-day L2 and trades available: `0`.
- Development dates excluded for technical/source availability reasons: `24`.
- Holdout registry source check:
  `PYTHONPATH=src python scripts/check_research_sources.py --registry data/manifests/research_dates.yaml --role holdout --timeout-seconds 6 --max-workers 8`
  checked 8 dates.
- Holdout dates with both same-day L2 and trades available: `0`.
- These legacy diagnostics are retained as history only. They must not be used
  as objective source unavailability evidence after the corrected GET probe.
- The pilot dates `2024-01-01`, `2024-02-01`, and `2024-03-01` previously could not be
  processed because the required primary Tardis public URLs returned HTTP 404
  and/or timed out. Example recorded failures:
  - `2024-01-01` L2 HEAD
    `https://datasets.tardis.dev/v1/binance/incremental_book_L2/2024/01/01/BTCUSDT.csv.gz`
    -> `404`; trades HEAD
    `https://datasets.tardis.dev/v1/binance/trades/2024/01/01/BTCUSDT.csv.gz`
    -> `TimeoutError`.
  - `2024-02-01` L2 HEAD
    `https://datasets.tardis.dev/v1/binance/incremental_book_L2/2024/02/01/BTCUSDT.csv.gz`
    -> `TimeoutError`; trades HEAD
    `https://datasets.tardis.dev/v1/binance/trades/2024/02/01/BTCUSDT.csv.gz`
    -> `404`.
  - `2024-03-01` L2 HEAD
    `https://datasets.tardis.dev/v1/binance/incremental_book_L2/2024/03/01/BTCUSDT.csv.gz`
    -> `TimeoutError`; trades HEAD
    `https://datasets.tardis.dev/v1/binance/trades/2024/03/01/BTCUSDT.csv.gz`
    -> `404`.
- Exact per-date source URLs, HEAD results, exclusion statuses, and exclusion
  reasons are recorded in `data/manifests/research_dates.yaml`.

Implemented pre-gate infrastructure:

- Registry creation/loading/writing utilities.
- Explicit Tardis source URL construction and vendor-symbol mapping.
- Source availability checker that records objective failed source requests.
- Per-day Phase 1-6 orchestration entry point with default
  `cross_day_features=false` and `cross_day_labels=false`.
- Multi-day registry driver that records failed dates and never silently omits
  them.
- Parquet writer/round-trip comparator for large derived artifacts.
- Deterministic artifact cache manifests using source checksum, config hash, and
  feature/label version.
- Large raw and derived artifacts remain ignored by Git; only metadata,
  fixtures, code, tests, and manifests are tracked.

Tests added for the pre-gate:

- Parquet round-trip preserving timestamp/null/value semantics.
- Day-boundary feature isolation.
- Day-boundary label isolation.
- Dataset-role isolation so development processing does not read holdout
  artifacts.
- Manifest/hash consistency.
- Cache invalidation for changed source checksum, config hash, feature version,
  label version, and stage.
- Partial failure handling that records the failed date and reason while
  preserving registry visibility.

Exact local test results after pre-gate infrastructure:

- `python -m pytest`: PASS, `86 passed, 1 warning in 1.35s`.
- `ruff check src tests scripts`: PASS, `All checks passed!`.
- `PYTHONPYCACHEPREFIX=/tmp/microalpha-pycache python -m compileall -q src scripts tests`:
  PASS.
- `PATH=/tmp/microalpha-config-smoke-venv/bin:$PATH microalpha-smoke --manifest-out /tmp/microalpha-smoke.yaml`:
  PASS, config hash
  `8199bdda9ceea7571824b87d0fcd1927d457efb258075075a853f9dfb8885bd0`.

GitHub Actions after pre-gate infrastructure commit:

- Pre-gate infrastructure commit SHA:
  `efd930ea91425864c8aaa35f8cc9f6f457794dc6`
  (`Add pre-Phase-7 multi-day gate infrastructure`).
- `tests` workflow: PASS.
  Run: `https://github.com/H2nryHe/Microstructure_Alpha_Execution_Lab/actions/runs/31285342173`.
- `research-smoke` workflow: PASS.
  Run: `https://github.com/H2nryHe/Microstructure_Alpha_Execution_Lab/actions/runs/31285342179`.

Legacy gate result (superseded):

- Pilot processing was not run because the mechanically selected pilot dates do
  not have both required primary source files available from the checked public
  URLs.
- Full 24-day development processing was not run for the same reason.
- No aggregate research snapshot manifest or snapshot hash was generated.
- No failed date was dropped; all failed source checks are retained in the
  registry.
- The 2026 holdout remains untouched by predictive research. Only source
  availability was checked and recorded.
- No IC, feature bucket study, feature-return relationship, model training,
  threshold tuning, ablation, backtest, trading logic, or Phase 7 work was run.

Legacy limitations / required decision before corrected GET retry (superseded):

- The requested primary unauthenticated Tardis public dataset URLs were not
  available for the frozen 2024-2025 development corpus during this run.
- Some source checks returned `TimeoutError`; those dates should be retried with
  a longer timeout, a Tardis API-enabled source path if available, or a
  deterministic replacement/source policy defined before any predictive
  analysis.
- The gate cannot pass until the selected corpus, or objectively documented
  deterministic replacements, can be processed through Phases 1-6 with same-day
  L2 and trades.

## Pre-Phase-7 Availability Checker Correction

Status: SOURCE VERIFICATION PASS + THREE-DATE PILOT PHASE 1-6 PASS locally.
The Pre-Phase-7 Multi-Day Expansion Gate remains BLOCKED pending full 24-day
Phase 1-6 processing and aggregate frozen snapshot generation. Phase 7 has not
started.

Local verification note:

- Local Python version for this correction update: `Python 3.10.9`.
- Current local results are not Python 3.11 compatibility evidence.
- No new GitHub Actions run was triggered or confirmed for the current
  uncommitted registry update.

Correction to prior conclusion:

- The previous conclusion that all 24 frozen Tardis development dates were
  objectively unavailable is superseded.
- The root cause was an availability checker that used authoritative HTTP
  `HEAD` probes. Tardis downloadable datasets are a `GET` endpoint, and HEAD
  404/timeout diagnostics are not sufficient evidence of actual dataset
  unavailability.
- Legacy HEAD diagnostics are retained in
  `data/manifests/research_dates.yaml` under `source_availability_history`.
- All 24 development dates were rechecked with the corrected low-concurrency
  GET + metadata path.
- Legacy HEAD diagnostics remain under `source_availability_history` and are
  not treated as objective source exclusions.
- Corrected registry state:
  - Development source-available / `included`: `24` dates.
  - Development `requires_recheck`: `0` dates.
  - Development `excluded`: `0` dates.
  - Metadata check `ok=true`: `24` development dates.
  - Non-pilot development dates still have Phase 1-6 processing statuses
    `pending`; `included` here means source-available, not fully processed.

Corrected source availability checker:

- Uses HTTP `GET`, not `HEAD`, for Tardis source probes.
- Uses a normal browser-compatible project User-Agent.
- Reads only the configured initial byte sample, default `2` bytes and `64`
  bytes for live diagnostics here, then closes the stream.
- Does not assume Range support. Range probing was tested and returned
  Cloudflare `403` for the known-good 2019 source, so the default probe is plain
  GET with early close.
- Records method, status, content headers, diagnostic headers, bytes read, first
  bytes, gzip signature result, elapsed time, exception details, redirects, and
  small textual error bodies.
- Uses explicit statuses: `AVAILABLE`, `CONFIRMED_UNAVAILABLE`,
  `TRANSIENT_ERROR`, `AUTH_REQUIRED`, and `CHECK_FAILED`.
- Retries transient failures with bounded exponential backoff.
- Timeouts and network errors produce `TRANSIENT_ERROR` and cannot permanently
  exclude a frozen date.
- `401` / `403` produce `AUTH_REQUIRED`, not "dataset missing".
- `404` is not treated as a permanent exclusion when Tardis metadata still
  indicates the symbol, date coverage, and data types should exist.

Known-good 2019 validation:

- L2 URL:
  `https://datasets.tardis.dev/v1/binance/incremental_book_L2/2019/12/01/BTCUSDT.csv.gz`
  - Method: `GET`
  - Status: `200`
  - Content-Type: `text/csv`
  - Content-Length: `43947405`
  - Bytes read: `64`
  - Gzip signature: `true`
  - `x-md5`: `"bd2c0f56f73bd9508b92535ebe3c249b"`
  - First bytes hex:
    `1f8b0800000000000203acbddbb2243b8e9e793fcf121646103cdecee80d46732d`
- Trades URL:
  `https://datasets.tardis.dev/v1/binance/trades/2019/12/01/BTCUSDT.csv.gz`
  - Method: `GET`
  - Status: `200`
  - Content-Type: `text/csv`
  - Content-Length: `6669039`
  - Bytes read: `64`
  - Gzip signature: `true`
  - `x-md5`: `"f7e5676fde021190b82756ec62074a89"`
  - First bytes hex:
    `1f8b0800000000000203acbdd9ce2dc9729877ef67696ce4109199716bfb0d2c5d`

Manual `2024-01-01` GET behavior:

- L2 URL:
  `https://datasets.tardis.dev/v1/binance/incremental_book_L2/2024/01/01/BTCUSDT.csv.gz`
  - Method: `GET`
  - Status: `200`
  - Content-Type: `text/csv`
  - Content-Length: `79021220`
  - Bytes read: `64`
  - Gzip signature: `true`
  - `x-md5`: `"db1c748517df066c3127ab61375edb54"`
  - Redirect URL: none
  - Error body: none
  - First bytes hex:
    `1f8b0800000000000203acbdcb92253b729e3bd7b3a42d83038edb54d21b1c9d`
- Trades URL:
  `https://datasets.tardis.dev/v1/binance/trades/2024/01/01/BTCUSDT.csv.gz`
  - Method: `GET`
  - Status: `200`
  - Content-Type: `text/csv`
  - Content-Length: `13066686`
  - Bytes read: `64`
  - Gzip signature: `true`
  - `x-md5`: `"a7ece8f3d15e77f4c1ca13ea646f83a6"`
  - Redirect URL: none
  - Error body: none
  - First bytes hex:
    `1f8b0800000000000203acbddd8e25bb8ee777ef67d928e893a46e6dbf8167ae8d`

Tardis metadata cross-check:

- Metadata endpoint checked:
  `https://api.tardis.dev/v1/exchanges/binance`.
- Exchange id: `binance`.
- Exchange `availableSince`: `2019-03-30T00:00:00.000Z`.
- Dataset `exportedUntil` observed during this correction run:
  `2026-08-09T00:00:00.000Z`.
- `BTCUSDT` metadata:
  - Symbol exists in dataset metadata.
  - Type: `spot`.
  - Available since: `2019-03-30T00:00:00.000Z`.
  - Available to: `2026-08-09T00:00:00.000Z`.
  - Supported data types include `trades` and `incremental_book_L2`.
  - Other listed data types: `quotes`, `book_snapshot_5`,
    `book_snapshot_25`, `book_ticker`.

Pilot availability recheck:

- Command:
  `PYTHONPATH=src python scripts/check_research_sources.py --registry data/manifests/research_dates.yaml --role development --date 2024-01-01 --date 2024-02-01 --date 2024-03-01 --timeout-seconds 30 --read-bytes 64 --max-attempts 3 --max-workers 1`.
- Result:
  - `available`: `2024-01-01`, `2024-02-01`, `2024-03-01`.
  - `not_available`: none.
- `2024-02-01` GET metadata:
  - L2 status `200`, content length `113798361`, bytes read `64`,
    gzip signature `true`, `x-md5`
    `"0db6dac8f41fce94d1d5aaf427092acf"`.
  - Trades status `200`, content length `15827091`, bytes read `64`,
    gzip signature `true`, `x-md5`
    `"bbee6a6e2fe342b938c80b2cb693952d"`.
- `2024-03-01` GET metadata:
  - L2 status `200`, content length `144163385`, bytes read `64`,
    gzip signature `true`, `x-md5`
    `"9c6a267be7f5b73a0339cb4f0109dae2"`.
  - Trades status `200`, content length `22046896`, bytes read `64`,
    gzip signature `true`, `x-md5`
    `"b5f27c8d3f1c59214017e8403dec91f5"`.

Full development availability recheck:

- Command:
  `PYTHONPATH=src python scripts/check_research_sources.py --registry data/manifests/research_dates.yaml --role development --timeout-seconds 20 --read-bytes 64 --max-attempts 3 --max-workers 1 --metadata-timeout-seconds 20`.
- Result:
  - `checked`: `24`.
  - `available`: all first-of-month development dates from `2024-01-01`
    through `2025-12-01`.
  - `not_available`: none.
  - Each development date has both `incremental_book_L2` and `trades` GET
    status `200`, `availability_status=AVAILABLE`, `bytes_read=64`, and gzip
    signature `true`.
  - Each development date has Tardis metadata check `ok=true`, including
    symbol coverage and support for both required data types.

Pilot Phase 1-6 execution:

- Command:
  `PYTHONPATH=src python scripts/run_research_registry.py --registry data/manifests/research_dates.yaml --role development --date 2024-01-01 --date 2024-02-01 --date 2024-03-01 --work-root /tmp/microalpha-multiday --source-root /tmp/microalpha-multiday/source --stop-on-error`.
- Result:
  - `processed`: `2024-01-01`, `2024-02-01`, `2024-03-01`.
  - `failed`: none.
  - `stop_on_error`: `true`.
- Large source/raw/bronze/derived artifacts were written only under `/tmp` and
  remain outside Git.

Pilot per-day results:

```text
date        l2_rows   trade_rows research_rows unavailable_research_rows feature_rows label_rows invalid_crossed feature_runtime_s label_runtime_s total_runtime_s feature_hash                                                      label_hash
2024-01-01  12284879  1114633    863986        0                         863986       863986     0               727.055           223.903         1850.275        c0e8e2387fe6cc1107962ffc9e5d977e76ace565b9d9c352b5a561ce23c4af6f d61e2ebcb617f8534bfd74bb524f610ebdce2f68d582dccbf5268732716a4ec2
2024-02-01  18878457  1392269    863980        0                         863980       863980     0               1499.197          242.909         3398.841        bfb8be02390943e2c659d4c3ba388c7129d4d28a3950280c87c858a268a8a10f 6350b78ee606d35de4e0399f5be1a6bf79e25bb1442c79ab370bfc5b3d425782
2024-03-01  23766560  1947370    863986        50                        863986       863986     0               1825.235          218.767         4676.323        24af17d47dee64200f23aa4d518b8fdefcb777f8a43afdaaef1ee83f25930b11 840f9ede0719b28030bff1973ee73d50ea4db7119cf56cd01d17482be5a27296
```

Pilot QA results:

- `2024-01-01` L2 QA: PASS, rows `12284879`, errors `0`, warnings `0`,
  duplicates `0`.
- `2024-01-01` trades QA: PASS, rows `1114633`, errors `0`, warnings `0`,
  duplicates `0`.
- `2024-02-01` L2 QA: PASS, rows `18878457`, errors `0`, warnings `0`,
  duplicates `0`.
- `2024-02-01` trades QA: PASS, rows `1392269`, errors `0`, warnings `0`,
  duplicates `0`.
- `2024-03-01` L2 QA: PASS, rows `23766560`, errors `0`, warnings `0`,
  duplicates `0`.
- `2024-03-01` trades QA: PASS, rows `1947370`, errors `0`, warnings `0`,
  duplicates `0`.

Implementation notes:

- `validate_market_data_csv` duplicate detection was hardened for full-day
  multi-million-row L2 files by replacing per-row sorted tuple retention with a
  deterministic SHA-256 row fingerprint over CSV field order. The previous
  implementation stalled during full-day 2024 QA after Phase 1; the corrected
  duplicate detector completed QA for all three pilot days.
- The full pilot run remains slow, especially Phase 5 feature generation:
  approximately `727.1`, `1499.2`, and `1825.2` seconds for the three dates.
  No causal feature logic was rewritten for speed.
- No IC, bucket returns, feature-return relationship, threshold tuning,
  strategy, model, backtest, or Phase 7 analysis was performed.

Tests required by the availability correction:

- GET `200` -> `AVAILABLE`.
- GET `206` -> `AVAILABLE`.
- GET `404` handling with metadata cross-check.
- GET `403` -> `AUTH_REQUIRED`, not missing.
- Timeout -> `TRANSIENT_ERROR`.
- Network error -> `TRANSIENT_ERROR`.
- Retry succeeds after transient failure.
- Gzip signature validation.
- Response body is not fully downloaded.
- Known-good 2019 Tardis URL logic.
- Metadata confirms known-good 2019 symbol/date/data types.
- Failed HEAD response no longer determines availability.
- Successful GET recheck clears stale HEAD-derived `not_run_source_unavailable`
  statuses.
- 404 plus supporting metadata requires recheck rather than permanent exclusion.
- 404 plus metadata date gap can produce a permanent exclusion.

Exact local test results for this correction:

- `python -m pytest`: PASS, `100 passed, 1 warning in 2.08s`.
- `ruff check src tests scripts`: PASS, `All checks passed!`.
- `python -m compileall src scripts`:
  PASS.
- `microalpha-smoke --manifest-out /tmp/microalpha-smoke.yaml`:
  initially not found on the default shell `PATH`.
- `PATH=/tmp/microalpha-config-smoke-venv/bin:$PATH microalpha-smoke --manifest-out /tmp/microalpha-smoke.yaml`:
  PASS, config hash
  `8199bdda9ceea7571824b87d0fcd1927d457efb258075075a853f9dfb8885bd0`.
- Equivalent source-tree entry-point command
  `PYTHONPATH=src python -m microalpha.cli --manifest-out /tmp/microalpha-smoke.yaml`:
  PASS, config hash
  `8199bdda9ceea7571824b87d0fcd1927d457efb258075075a853f9dfb8885bd0`.

GitHub Actions for the prior committed availability correction:

- Correction commit SHA:
  `1c1261497d224ce5e2c4346411fadb40ad0f12ba`
  (`Fix Tardis availability probing`).
- CI Python version: GitHub Actions workflows use `actions/setup-python@v5` with
  `python-version: "3.11"`.
- `tests` workflow: PASS.
  Run: `https://github.com/H2nryHe/Microstructure_Alpha_Execution_Lab/actions/runs/31292717831`.
- `research-smoke` workflow: PASS.
  Run: `https://github.com/H2nryHe/Microstructure_Alpha_Execution_Lab/actions/runs/31292717888`.
- Current registry update has not been committed or pushed, so no GitHub
  Actions result exists for it yet.

Next required work before Phase 7:

- Process the remaining 21 source-available development dates through the same
  Phase 1-6 pipeline.
- Only after the full frozen development corpus is processed, or objective
  corrected-GET/metadata-backed exclusions are documented, generate the
  aggregate frozen research snapshot.
- Do not begin Phase 7 until the full Pre-Phase-7 Multi-Day Expansion Gate
  passes.

## Pre-Phase-7 Multi-Day Expansion Gate

Status: PASS locally; final push and new Python 3.11 GitHub Actions confirmation
are pending because the current environment rejected further escalated process
operations after the local work completed.

Scope:

- No Phase 7 work was started.
- No IC, bucket studies, feature-return relationships, model training, strategy
  metrics, or backtests were calculated.
- `cross_day_features=false` and `cross_day_labels=false` remain set in
  `data/manifests/research_dates.yaml`.
- The 2026 holdout dates remain excluded from predictive research and are absent
  from the frozen development snapshot.

Pilot verification:

- `2024-01-01`, `2024-02-01`, and `2024-03-01` were explicitly verified as
  Phase 1-6 complete.
- For each pilot date: source availability PASS, ingestion PASS, QA PASS,
  book replay PASS, research dataset PASS, feature generation PASS, label
  generation PASS, feature hash exists, label hash exists, artifact paths exist,
  source checksums exist, feature version matches config, and label version
  matches config.

Remaining development-date processing:

- Processed remaining frozen development dates `2024-04-01` through
  `2025-12-01` with the existing Phase 1-6 driver.
- Large raw/bronze/derived CSV working files remained outside Git under
  `/tmp/microalpha-multiday`.
- For each successful non-pilot date, cleanup retained source gzip files,
  QA reports, day/artifact manifests, label summaries, and Parquet outputs;
  cleanup removed raw working copies, bronze CSVs, and derived CSV
  intermediates.
- Remaining-date runner result: processed `21`, failed `0`, retried `0`.
- Remaining-date total runtime: `57320.429` seconds.
- Slowest remaining dates were `2025-08-01` at `6163.534` seconds and
  `2025-12-01` at `5429.851` seconds, dominated by large L2 validation/replay
  and Phase 5 feature generation. Treat Phase 5/runtime as future Phase 16
  optimization work; no financial logic was optimized during this gate.

Development corpus completeness:

- Included dates: all 24 first-of-month development dates from `2024-01-01`
  through `2025-12-01`.
- Excluded dates: none.
- Failed dates: none.
- Pending dates: none.
- Per-date source checksums, row counts, QA status, replay counts, feature
  hashes, label hashes, runtimes, versions, and artifact paths are recorded in
  `data/manifests/pre_phase7_verification.json`.
- Row-count range across included dates:
  - research rows: `863950` to `863992`.
  - feature rows: `863950` to `863992`.
  - label rows: `863950` to `863992`.
  - unavailable/stale research rows: `0` to `50`.
  - crossed/invalid book states: `0` on every included date.

Multi-day feature QA:

- Report: `data/manifests/pre_phase7_feature_qa.json`.
- Important features reviewed per date: `qi_1`, `di_5`, `di_10`,
  `spread_bps`, `microprice_deviation_bps`, `ofi_1s`,
  `trade_imbalance_1s`, `realized_vol_5s`, and `mom_1s`.
- Reported per-date missing rate, p1, median, p99, and constant-feature flag.
- Review note: no constant important features and no impossible crossed-book
  replay counts were observed. `trade_imbalance_1s` missingness exceeded a
  5% review threshold on 12 dates, ranging from about `5.18%` to `11.02%`.
  This was recorded as data/feature QA only and was not used to drop dates or
  tune any label/feature logic.

Multi-day label QA:

- Report: `data/manifests/pre_phase7_label_qa.json`.
- For every included date and label horizon, the report records valid count,
  missing count, missing percentage, UP/FLAT/DOWN percentages, median lookup
  delay, p95 lookup delay, and maximum accepted lookup delay.
- No label missing percentage exceeded `1%` in the generated review.
- The configured `0.5` bps threshold was not tuned from these results.
- Class proportions were not interpreted as signal performance.

Frozen aggregate research snapshot:

- Manifest: `data/manifests/pre_phase7_research_snapshot.json`.
- Snapshot version: `pre_phase7_research_snapshot_v1`.
- Dataset role: `development`.
- Canonical instrument: `BTC-USDT`.
- Vendor: `tardis_binance_spot`.
- Included dates: all 24 development dates, ordered.
- Excluded dates: none.
- Failed dates: none.
- Snapshot hash:
  `0bcdb7eddebbe83458998eff78844471afb78fc66d249a53aeb25667bebd803a`.
- Snapshot hash determinism checks:
  - same inputs produce the same hash: PASS.
  - creation timestamp is excluded from the hash: PASS.
  - controlled dependency change changes the hash: PASS.
  - absolute local paths such as `/tmp/...` do not affect the hash: PASS.
  - holdout dates are absent from the included-date list: PASS.
- This snapshot is the frozen development input for Phase 7. Any later
  data/config/code change requires a new snapshot version/hash before Phase 7
  uses it.

Local test results after full multi-day processing:

- `python -m pytest`: PASS, `104 passed, 1 warning in 2.20s`.
- `ruff check src tests scripts`: PASS, `All checks passed!`.
- `python -m compileall -q src scripts tests`: PASS.
- `PATH=/tmp/microalpha-config-smoke-venv/bin:$PATH microalpha-smoke --manifest-out /tmp/microalpha-smoke.yaml`:
  PASS, config hash
  `8199bdda9ceea7571824b87d0fcd1927d457efb258075075a853f9dfb8885bd0`.

Python 3.11 CI status:

- Source-verification support commit
  `4cbb6f0958fc57134913a8490c1385beb4a688e1` was pushed to `origin/main`.
- GitHub Actions `tests` run `31293453728`: PASS.
  URL:
  `https://github.com/H2nryHe/Microstructure_Alpha_Execution_Lab/actions/runs/31293453728`.
- GitHub Actions `research-smoke` run `31293453718`: PASS.
  URL:
  `https://github.com/H2nryHe/Microstructure_Alpha_Execution_Lab/actions/runs/31293453718`.
- Both workflows use `actions/setup-python@v5` with Python `3.11`.
- The current registry/report/status update has not yet been committed or
  pushed, so there is not yet a GitHub Actions result for this exact final
  artifact state.

Assumptions and limitations:

- Local execution used Python 3.10.9 and emitted the existing pandas warning that
  installed `bottleneck` is `1.3.5` while pandas asks for `>=1.3.6`.
- Python 3.11 compatibility for source code is evidenced by GitHub Actions on
  the pushed source-verification commit; final artifact-state CI still needs a
  successful push and workflow run.
- `/tmp/microalpha-multiday` contains the retained local source gzip files and
  Parquet outputs used to generate the manifests. These large files are not
  committed to Git.
- The aggregate snapshot intentionally hashes logical checksums, configs,
  versions, and ordered dates, not absolute local artifact paths.

Next steps:

- Commit and push the current registry/report/status update when Git operations
  are available.
- Confirm GitHub Actions `tests` and `research-smoke` pass on the resulting
  commit under Python 3.11.
- Do not begin Phase 7 until that final current-commit CI confirmation is green.

## Phase 7 - Baseline Statistical Signal Research

Status: PASS locally.

Scope:

- Phase 7 used only the frozen development dates from `2024-01-01` through
  `2025-12-01`.
- The 2026 holdout was not read or referenced by the Phase 7 runner.
- No model training, optimization, feature-definition tuning, label-threshold
  tuning, trading rule, backtest, execution simulation, or Phase 8 work was
  performed.

Frozen inputs and plan:

- Immutable project specification remains unchanged.
- Frozen snapshot:
  `data/manifests/pre_phase7_research_snapshot.json`.
- Required and verified snapshot hash:
  `0bcdb7eddebbe83458998eff78844471afb78fc66d249a53aeb25667bebd803a`.
- Snapshot verification confirmed 24 ordered development dates, no excluded
  dates, no failed dates, dataset role `development`, feature version
  `microstructure_v1`, label version `microstructure_labels_v1`, and no 2026
  holdout dates.
- Frozen Phase 7 research plan:
  `data/manifests/phase7_research_plan.yaml`.
- Phase 7 research plan hash:
  `417f1b38895bf1cc3735cb72ce08249a2cc32ba7cfc936eec5a1875dea0e14da`.
- Primary matrix: 30 prespecified tests, covering `qi_1`, `di_5`, `di_10`,
  `microprice_deviation_bps`, and horizon-matched OFI/trade-imbalance features
  against `ret_fwd_100ms`, `ret_fwd_500ms`, `ret_fwd_1s`, `ret_fwd_5s`, and
  `ret_fwd_30s`.

Implementation and outputs:

- Added Phase 7 utilities in `src/microalpha/research/phase7.py`.
- Added Phase 7 runner `scripts/run_phase7_research.py`.
- Added unit tests in `tests/unit/test_phase7_research.py`.
- Added tracked plan allow-list entry in `.gitignore`.
- Generated compact Phase 7 outputs under `reports/phase7/`:
  - `primary_ic.csv`: 30 rows.
  - `daily_ic.csv`: 720 rows.
  - `nonoverlap_ic.csv`: 750 rows.
  - `bucket_results.csv`: 9000 rows.
  - `next_move_results.csv`: 1250 rows.
  - `direction_results.csv`: 7500 rows.
  - `phase7_summary.json`.
  - `README.md`.
  - 10 prespecified figures under `reports/phase7/figures/`.
- Deterministic Phase 7 result hash:
  `b86d51c4317f87d0cabf579f152d07c139e7fc23e47356d655bd09057342eb04`.
- A fresh recomputation of the result hash matched the stored summary hash.

Statistical summary:

- All 30 primary tests had positive mean daily Spearman IC.
- Minimum BH/FDR q-value across the 30-test primary family:
  `1.4326671839881356e-21`.
- Maximum absolute mean daily Spearman IC:
  `0.4259401679798902`.
- Strongest mean daily IC:
  `qi_1` vs `ret_fwd_1s`, mean Spearman IC `0.425940168`, median
  `0.4364408196`, 24 positive days, 0 negative days, t-stat
  `39.1582477124`, FDR q-value `1.432667184e-21`.
- Other top primary tests were `microprice_deviation_bps` vs `ret_fwd_1s`
  with mean Spearman IC `0.4258462052`, and `di_5` vs `ret_fwd_1s` with mean
  Spearman IC `0.4238563992`.
- 2024/2025 split stability: all 30 primary tests had same-sign annual mean ICs.
- Non-overlap robustness: mean absolute full-grid vs non-overlap IC difference
  across primary summaries was `0.0009773737819616633`.
- Negative control: deterministic within-day permutation of `qi_1` vs
  `ret_fwd_1s` had mean Spearman IC `0.0001328308230472829`, t-stat
  `0.7036037327952906`, raw p-value `0.48874197127979135`, 15 positive days,
  and 9 negative days.

Missing-data and inference policy:

- Missing alpha features and labels were handled pairwise-valid only.
- Missing alpha features were not filled with zero.
- Daily Spearman IC is the primary inference metric with day as the inference
  unit.
- Pearson IC is reported as a secondary diagnostic.
- Pooled cross-day IC is not used for inference.
- Bucket studies rank valid feature observations within each day using feature
  values only, then evaluate labels within those fixed deciles using equal
  day weighting.

Tests added:

- Spearman/Pearson utility determinism.
- Pairwise missing handling and no-zero-fill guard.
- t-statistic, sign consistency, and sign-test behavior.
- Benjamini-Hochberg/FDR behavior.
- Deterministic decile buckets with ties, top-minus-bottom effect, and bucket
  monotonicity.
- Next-mid-move unavailable-outcome exclusion.
- Non-overlap deterministic offset-zero mask.
- Deterministic within-day permutation.
- Snapshot verification, holdout rejection, chronological development-date
  ordering, and 2024/2025 split rejection of 2026 dates.
- Deterministic result hash excluding embedded result-hash self-reference.
- Exact 30-test primary matrix.

Exact local test results:

- `python -m pytest`: PASS, `119 passed, 1 warning in 2.00s`.
- `ruff check src tests scripts`: PASS, `All checks passed!`.
- `python -m compileall -q src scripts tests`: PASS.
- `PATH=/tmp/microalpha-config-smoke-venv/bin:$PATH microalpha-smoke --manifest-out /tmp/microalpha-smoke.yaml`:
  PASS, config hash
  `8199bdda9ceea7571824b87d0fcd1927d457efb258075075a853f9dfb8885bd0`.
- `MPLCONFIGDIR=/tmp/microalpha-mpl PYTHONPATH=src python scripts/run_phase7_research.py --clean`:
  PASS, result hash
  `b86d51c4317f87d0cabf579f152d07c139e7fc23e47356d655bd09057342eb04`.

Assumptions and limitations:

- Local execution used Python 3.10 and emitted the existing pandas warning that
  installed `bottleneck` is `1.3.5` while pandas asks for `>=1.3.6`.
- PyArrow emitted sandbox CPU-info warnings while reading parquet; these did not
  affect the Phase 7 gate.
- Phase 7 evidence is statistical predictability on the frozen development
  sample only and is not an executable trading result.
- No suspicious-audit exception was opened from Phase 7 outputs because the
  negative control was near zero, non-overlap robustness was close to full-grid
  IC, and 2024/2025 signs were stable. The high same-sign primary family should
  still be treated as research evidence only, not as a trading claim.
- Python 3.11 CI was later confirmed on the exact pushed Phase 7 audit artifact
  commit recorded below.

Next steps:

- See the Phase 7 audit section for the exact pushed commit and Python 3.11 CI
  runs that cleared the pre-Phase-8 gate.

## Phase 7 Suspicious-Result / Robustness Audit

Status: PASS locally.

Scope:

- This was a narrowly scoped audit of the unusually strong Phase 7 baseline
  results.
- Original Phase 7 baseline outputs were preserved and not overwritten:
  `primary_ic.csv`, `bucket_results.csv`, `nonoverlap_ic.csv`,
  `phase7_summary.json`, existing figures, the Phase 7 research plan, and the
  Phase 7 result hash remain unchanged.
- Fresh recomputation confirmed the original Phase 7 result hash remains:
  `b86d51c4317f87d0cabf579f152d07c139e7fc23e47356d655bd09057342eb04`.
- No 2026 holdout data was accessed.
- No Phase 8 work, model training, optimization, trading rule, backtest, or
  execution simulation was performed.

Audit implementation and outputs:

- Added `scripts/run_phase7_audit.py`.
- Created audit outputs under `reports/phase7/audit/` only:
  - `audit_summary.json`.
  - `changed_state_ic.csv`.
  - `unique_state_ic.csv`.
  - `manual_lineage_audit.csv`.
  - `independent_label_check.csv`.
  - `independent_bucket_check.csv`.
  - `feature_redundancy.csv`.
  - `return_discreteness.csv`.
  - `spread_diagnostics.csv`.
  - `README.md`.
- Audit output size: approximately `352K`.
- Audit result hash:
  `b6b8206e03c81b47787d5ae4d4e5b960b4748bc75eed0ee5be4862ebf190d6e1`.
- Fresh recomputation of the audit hash matched the stored
  `audit_summary.json` hash.

Non-overlap reporting clarification:

- The prior Phase 7 summary value was confirmed to be the aggregate-row
  diagnostic over `date == ALL` rows.
- `aggregate_pair_mean_abs_difference`:
  `0.0009773737819900022`.
- `daily_pair_mean_abs_difference` across 24 x 30 daily comparisons:
  `0.003750858043641231`.
- Median daily absolute difference:
  `0.0015011638109999892`.
- P95 daily absolute difference:
  `0.01842234572084997`.
- Maximum daily absolute difference:
  `0.03277160954699998`.

Changed-state and unique-state robustness:

- Changed-state definition used deterministic consecutive BBO-state changes:
  `best_bid`, `bid_sz_1`, `best_ask`, and `ask_sz_1`.
- Top-10 book levels are available in the research table, but the audit used
  BBO identity to test the minimum explicitly required observable state.
- Changed-state retained median fraction of fixed-clock rows:
  `0.7462537598881`.
- Changed-state state-signal tests with positive mean IC:
  `20 / 20`.
- Minimum changed-state mean IC across state-signal/horizon summaries:
  `0.2164018712435578`.
- Unique consecutive BBO-state run collapse retained median fraction:
  `0.7462549173146528`.
- Unique-state state-signal tests with positive mean IC:
  `20 / 20`.
- Minimum unique-state mean IC across state-signal/horizon summaries:
  `0.21640109056283144`.
- A material magnitude decline would not have failed the audit; the observed
  changed/unique-state diagnostics remained directionally consistent.

Manual timestamp / lineage and independent labels:

- Manual lineage audit rows: `20`, selected deterministically across 2024 early,
  2024 late, 2025 early, and 2025 late, spanning very negative, moderately
  negative, near-zero, moderately positive, and very positive `qi_1`.
- All selected rows satisfied:
  - feature source observation time <= feature cutoff `T`;
  - target time > `T`;
  - actual label time >= target time;
  - actual label delay <= configured 100ms tolerance;
  - future mid came from a later/future state, not the feature state.
- Independent label recomputation rows: `80`, covering `100ms`, `1s`, `5s`,
  and `30s` horizons for the selected observations.
- Independent label recomputation failures: `0`.
- Maximum absolute label difference:
  `9.573505183047004e-17` with tolerance `1e-12`.
- The independent label check directly located future mids from the research
  table and did not call the production label-generation helper.

Independent bucket and aggregation audit:

- Independent `qi_1` / `1s` decile reconstruction was run for:
  `2024-01-01`, `2024-06-01`, `2024-12-01`, `2025-06-01`, and `2025-12-01`.
- Bucket failures: `0`.
- Maximum independent-vs-production mean return absolute difference:
  `3.581255300991182e-17`.
- Maximum independent-vs-production mean future-move bps absolute difference:
  `4.642120021713936e-13`.
- Bucket numbering was verified as low feature to high feature.
- Labels were not used for bucket assignment.
- No future-return sorting was used in the independent reconstruction.
- Equal-day aggregation was checked over 300 production aggregate bucket rows.
- Maximum equal-day mean-return aggregation difference:
  `5.083417410969848e-16`.
- Maximum equal-day mean-move aggregation difference:
  `4.958256027975949e-12`.

Feature redundancy and incremental diagnostics:

- The mathematical relationship was documented:
  `microprice - mid = spread * qi_1 / 2`, therefore
  `microprice_deviation_bps = spread_bps * qi_1 / 2`.
- Mean daily rank correlation between `qi_1` and
  `microprice_deviation_bps` was `0.999265480102078`.
- `qi_1` and `microprice_deviation_bps` should be treated as highly redundant
  transformations under the observed spread regime, not independent alpha
  discoveries.
- `feature_redundancy.csv` also records daily feature-feature correlations and
  descriptive rank-residual IC diagnostics for `di_5`, `di_10`, and `ofi_1s`
  residualized against `qi_1`.

Return discreteness, spread, and temporal controls:

- `return_discreteness.csv` reports per-date/horizon zero-return fraction,
  unique forward-return count, unchanged-mid fraction, and by-`qi_1`-decile
  zero/up/down fractions.
- `spread_diagnostics.csv` reports per-date spread summaries and `qi_1` IC
  separately for minimum-spread and wider-spread states.
- The deterministic temporal negative control was chosen before running:
  `qi_1` lagged by 5 minutes on the 100ms fixed grid.
- Temporal control mean Spearman IC:
  `0.004108648248610643`, much weaker than the primary aligned `qi_1` / `1s`
  mean IC of about `0.42594`.
- Temporal control t-stat:
  `4.207552744782699`, raw p-value `0.00033565848441401787`, with 21 positive
  and 3 negative days. This indicates residual temporal autocorrelation, but a
  large attenuation versus the aligned signal.

Exact local verification:

- `PYTHONPATH=src python scripts/run_phase7_audit.py --clean`:
  PASS, audit hash
  `b6b8206e03c81b47787d5ae4d4e5b960b4748bc75eed0ee5be4862ebf190d6e1`.
- Phase 7 baseline hash recomputation:
  PASS, unchanged at
  `b86d51c4317f87d0cabf579f152d07c139e7fc23e47356d655bd09057342eb04`.
- Audit hash recomputation:
  PASS, matched stored hash
  `b6b8206e03c81b47787d5ae4d4e5b960b4748bc75eed0ee5be4862ebf190d6e1`.
- `python -m pytest`: PASS, `119 passed, 1 warning in 2.58s`.
- `ruff check src tests scripts`: PASS, `All checks passed!`.
- `python -m compileall -q src scripts tests`: PASS.
- `PATH=/tmp/microalpha-config-smoke-venv/bin:$PATH microalpha-smoke --manifest-out /tmp/microalpha-smoke.yaml`:
  PASS, config hash
  `8199bdda9ceea7571824b87d0fcd1927d457efb258075075a853f9dfb8885bd0`.

Assumptions and limitations:

- Local execution used Python 3.10.9 and emitted the existing pandas warning
  that installed `bottleneck` is `1.3.5` while pandas asks for `>=1.3.6`.
- PyArrow emitted sandbox CPU-info warnings while reading parquet; these did not
  affect the audit gate.
- The changed-state and unique-state diagnostics used consecutive BBO identity.
  They intentionally did not use future labels.
- The audit did not reinterpret Phase 7 as an economic result. It only tested
  timestamp lineage, label construction, repeated-state weighting, bucket
  mechanics, feature redundancy, discreteness, spread mechanics, and temporal
  alignment sensitivity.
- Python 3.11 CI was confirmed on the exact pushed audit artifact commit.

Next steps:

- Exact pushed audit artifact commit:
  `d785b28907776865ebd1ca799cfd6ad1611e3717`.
- Commit message:
  `docs: Add Phase 7 robustness audit results to STATUS.md`.
- `origin/main` and local `HEAD` both resolved to that SHA before Phase 8 work
  started.
- GitHub Actions workflows use `actions/setup-python@v5` with
  `python-version: "3.11"`.
- GitHub Actions `tests` workflow: PASS.
  Run: `31346275365`.
  URL:
  `https://github.com/H2nryHe/Microstructure_Alpha_Execution_Lab/actions/runs/31346275365`.
  Head SHA:
  `d785b28907776865ebd1ca799cfd6ad1611e3717`.
- GitHub Actions `research-smoke` workflow: PASS.
  Run: `31346275370`.
  URL:
  `https://github.com/H2nryHe/Microstructure_Alpha_Execution_Lab/actions/runs/31346275370`.
  Head SHA:
  `d785b28907776865ebd1ca799cfd6ad1611e3717`.
- Phase 8 may now begin using only 2024 TRAIN and 2025 VALIDATION data.
- Do not access 2026 holdout data or begin Phase 9.

## Phase 8 - Baseline Predictive Modeling

Status: PASS locally

Pre-Phase-8 CI gate:

- Exact pushed Phase 7 audit artifact commit:
  `d785b28907776865ebd1ca799cfd6ad1611e3717`.
- GitHub Actions workflows use `actions/setup-python@v5` with
  `python-version: "3.11"`.
- GitHub Actions `tests` workflow: PASS.
  Run: `31346275365`.
  URL:
  `https://github.com/H2nryHe/Microstructure_Alpha_Execution_Lab/actions/runs/31346275365`.
  Head SHA:
  `d785b28907776865ebd1ca799cfd6ad1611e3717`.
- GitHub Actions `research-smoke` workflow: PASS.
  Run: `31346275370`.
  URL:
  `https://github.com/H2nryHe/Microstructure_Alpha_Execution_Lab/actions/runs/31346275370`.
  Head SHA:
  `d785b28907776865ebd1ca799cfd6ad1611e3717`.

Frozen plan:

- Plan file: `data/manifests/phase8_modeling_plan.yaml`.
- Phase 8 modeling plan hash:
  `823ee7a98be9a5199842536a65edd2394a39f1c5c6ae13947c29bd7c1c2494fe`.
- Frozen Phase 7 snapshot hash:
  `0bcdb7eddebbe83458998eff78844471afb78fc66d249a53aeb25667bebd803a`.
- Phase 7 result hash:
  `b86d51c4317f87d0cabf579f152d07c139e7fc23e47356d655bd09057342eb04`.
- Phase 7 audit hash:
  `b6b8206e03c81b47787d5ae4d4e5b960b4748bc75eed0ee5be4862ebf190d6e1`.
- The plan was created before Phase 8 validation model results were generated.

Data split and target:

- TRAIN dates: all 2024 first-of-month development dates,
  `2024-01-01` through `2024-12-01`.
- VALIDATION dates: all 2025 first-of-month development dates,
  `2025-01-01` through `2025-12-01`.
- 2025 is development validation, not an untouched holdout.
- 2026 holdout access: `false`.
- Primary target: `ret_fwd_1s`.
- Secondary classification target: `next_mid_change_direction`, with
  unavailable observations excluded.
- Primary anchor rule: deterministic non-overlapping 1s anchors from the 100ms
  grid, `row_index % 10 == 0`, offset `0`.
- Training rows: `1,036,749`.
- Validation rows: `1,036,771`.
- Classification validation rows: `911,358`.

Feature sets:

- `qi_only`: `qi_1`.
- `qi_ofi`: `qi_1`, `ofi_1s`.
- `qi_trade_imbalance`: `qi_1`, `trade_imbalance_1s`.
- `core_independent_microstructure`: `qi_1`, `ofi_1s`,
  `trade_imbalance_1s`.
- `extended_book_flow`: `qi_1`, `di_5`, `di_10`, `ofi_1s`,
  `trade_imbalance_1s`, `spread_bps`, `realized_vol_5s`, `mom_1s`,
  `book_update_count_1s`, `trade_count_1s`.
- Reference baselines: `ofi_1s` only and `trade_imbalance_1s` only.
- `microprice_deviation_bps` was excluded from primary models because the
  Phase 7 audit showed it is nearly rank-equivalent to `qi_1` under the
  observed spread regime.

Model configs:

- Null regression: training-set mean predictor.
- QI baseline: single-feature standardized ridge with `alpha=0.0`,
  `solver=svd`.
- Ridge regression: `alpha=1.0`, standardized features, training-only median
  imputation, explicit missing indicators for features missing in TRAIN.
- LightGBM regression: `n_estimators=120`, `learning_rate=0.05`,
  `num_leaves=15`, `max_depth=4`, `min_child_samples=200`, `subsample=0.8`,
  `colsample_bytree=0.9`, `reg_alpha=0.1`, `reg_lambda=1.0`,
  `random_state=8008`, `deterministic=true`, `force_col_wise=true`,
  `n_jobs=1`.
- Classification: QI-only logistic regression, core-feature logistic
  regression, and LightGBM classifier with the same tree parameters.

Implementation and outputs:

- Added reusable Phase 8 helpers in `src/microalpha/research/phase8.py`.
- Added runner `scripts/run_phase8_modeling.py`.
- Added leakage/isolation/modeling tests in
  `tests/unit/test_phase8_modeling.py`.
- Created compact tracked outputs under `reports/phase8/`:
  `regression_results.csv`, `daily_regression_metrics.csv`,
  `ablation_results.csv`, `prediction_correlations.csv`,
  `feature_importance.csv`, `classification_results.csv`,
  `classification_calibration.csv`, `negative_control.csv`,
  `phase8_summary.json`, `README.md`, and required figures.
- Phase 8 result hash:
  `d8471add338d79106fb1839008c5168535bb644505b5282a5e6236147b31255d`.

Validation metrics:

- QI baseline mean daily Spearman IC:
  `0.4222578166269703`.
- Best Ridge model: `ridge / qi_only`.
  Spearman IC `0.424256903031`, mean daily IC `0.422257816627`,
  Pearson `0.274089676645`, MAE `2.58063266482e-05`, RMSE
  `5.04536924006e-05`, R2 `0.0712236661297`, non-zero sign accuracy
  `0.867339596343`, positive validation days `12 / 12`.
- Best incremental model: `lightgbm_regression / extended_book_flow`.
  Spearman IC `0.437010448125`, mean daily IC `0.430171064696`,
  Pearson `0.337969588481`, MAE `2.17870943295e-05`, RMSE
  `4.9273301211e-05`, R2 `0.114173740143`, non-zero sign accuracy
  `0.870892101463`, positive validation days `12 / 12`.

Incremental lift over QI:

- `lightgbm_regression / extended_book_flow`: mean delta daily IC
  `0.00791324806945`, median `0.00832294095587`, min
  `0.00177382728405`, max `0.0149799477797`, positive-lift days `12`,
  negative-lift days `0`.
- `lightgbm_regression / core_independent_microstructure`: mean delta
  `0.00502243518434`, median `0.00498025879287`, min
  `-0.00122501618755`, max `0.0115030072751`, positive-lift days `11`,
  negative-lift days `1`.
- `lightgbm_regression / qi_ofi`: mean delta `0.00484766312093`, median
  `0.00421298509425`, min `-0.00163878344482`, max `0.0104240363204`,
  positive-lift days `11`, negative-lift days `1`.
- Ridge multivariate variants did not improve mean daily IC over QI-only.

Prediction redundancy:

- QI-only ridge prediction rank correlation with `qi_1`: `1.0`.
- LightGBM QI-only prediction rank correlation with `qi_1`:
  `0.999794341414`.
- LightGBM `core_independent_microstructure` prediction rank correlation:
  `qi_1=0.960831733082`, `ofi_1s=0.629918550616`,
  `trade_imbalance_1s=0.316948322822`.
- LightGBM `extended_book_flow` prediction rank correlation:
  `qi_1=0.950510869209`, `ofi_1s=0.625371024709`,
  `trade_imbalance_1s=0.312459706788`.
- Interpretation: the best model remains heavily related to QI, with modest
  incremental information from flow/deeper-book features.

Classification metrics:

- `logistic_qi / qi_only`: ROC AUC `0.763726139363`, log loss
  `0.584211503425`, Brier `0.199419383388`.
- `logistic_core / core_independent_microstructure`: ROC AUC
  `0.762554132261`, delta AUC `-0.00117200710254`, log loss
  `0.583598394067`, delta log loss `-0.000613109357836`, Brier
  `0.199174840627`, delta Brier `-0.000244542761368`.
- `lightgbm_classifier / core_independent_microstructure`: ROC AUC
  `0.764691788037`, delta AUC `0.000965648673324`, log loss
  `0.577684004165`, delta log loss `-0.00652749926056`, Brier
  `0.197286487243`, delta Brier `-0.00213289614558`.
- No decision-threshold tuning was performed.

Negative control:

- Control: deterministic permuted train target with ridge on
  `core_independent_microstructure`, bounded to `200,000` train rows and
  `200,000` validation rows.
- Validation against permuted target: Spearman IC `0.0022175796`, Pearson
  `0.0018329581`, R2 `-1.74617e-05`, non-zero sign accuracy
  `0.5024767963`.
- The diagnostic rank correlation against the real target was also recorded
  separately as `0.2265100018`; it is not the negative-control target metric.

Exact local test results:

- `MPLCONFIGDIR=/tmp/microalpha-mpl PYTHONPATH=src /tmp/microalpha-phase8-venv/bin/python scripts/run_phase8_modeling.py --clean`:
  PASS, result hash
  `d8471add338d79106fb1839008c5168535bb644505b5282a5e6236147b31255d`.
- `python -m pytest`: PASS, `126 passed, 37 warnings in 3.89s`.
- `ruff check src tests scripts`: PASS, `All checks passed!`.
- `python -m compileall -q src scripts tests`: PASS.
- `PATH=/tmp/microalpha-config-smoke-venv/bin:$PATH microalpha-smoke --manifest-out /tmp/microalpha-smoke.yaml`:
  PASS, config hash
  `8199bdda9ceea7571824b87d0fcd1927d457efb258075075a853f9dfb8885bd0`.
- Forbidden economic-word scan over `STATUS.md`, `reports/phase8`,
  `scripts/run_phase8_modeling.py`, and
  `data/manifests/phase8_modeling_plan.yaml`: PASS, no matches.

Assumptions and limitations:

- Local default `python` is Python 3.10.9, not Python 3.11.
- Phase 8 LightGBM execution used a temporary environment at
  `/tmp/microalpha-phase8-venv` with working LightGBM dependencies.
- PyArrow emitted sandbox CPU-info warnings while reading parquet; they did not
  affect the Phase 8 gate.
- The accepted Phase 8 artifact state was later confirmed by GitHub Actions
  under Python 3.11 on commit
  `0cff6ce05980ac226ec47f0d602045a6dadf9993`.
- 2025 is development validation because Phase 7 already examined 2025.
- No 2026 holdout date was read.
- No Phase 9 walk-forward evaluation, trading signal, execution threshold,
  fill simulation, cost analysis, PnL, or backtest was implemented.

Next steps:

- Pre-Phase-9 GitHub Actions gate was cleared on exact Phase 8 commit
  `0cff6ce05980ac226ec47f0d602045a6dadf9993`.
- GitHub Actions `tests`: PASS, run `31351466257`.
- GitHub Actions `research-smoke`: PASS, run `31351466255`.
- Do not begin Phase 10 until the user explicitly accepts Phase 9 and requests
  continuation.

## Phase 9 - Walk-Forward Temporal Robustness

Status: PASS locally

Pre-Phase-9 CI gate:

- Exact accepted Phase 8 artifact commit:
  `0cff6ce05980ac226ec47f0d602045a6dadf9993`.
- Remote `main` resolved to the same SHA before Phase 9 work began.
- GitHub Actions workflows use `actions/setup-python@v5` with
  `python-version: "3.11"`.
- GitHub Actions `tests` workflow: PASS.
  Run: `31351466257`.
  URL:
  `https://github.com/H2nryHe/Microstructure_Alpha_Execution_Lab/actions/runs/31351466257`.
  Head SHA:
  `0cff6ce05980ac226ec47f0d602045a6dadf9993`.
- GitHub Actions `research-smoke` workflow: PASS.
  Run: `31351466255`.
  URL:
  `https://github.com/H2nryHe/Microstructure_Alpha_Execution_Lab/actions/runs/31351466255`.
  Head SHA:
  `0cff6ce05980ac226ec47f0d602045a6dadf9993`.

Frozen inputs and plan:

- Phase 7 snapshot hash:
  `0bcdb7eddebbe83458998eff78844471afb78fc66d249a53aeb25667bebd803a`.
- Phase 7 result hash:
  `b86d51c4317f87d0cabf579f152d07c139e7fc23e47356d655bd09057342eb04`.
- Phase 7 audit hash:
  `b6b8206e03c81b47787d5ae4d4e5b960b4748bc75eed0ee5be4862ebf190d6e1`.
- Phase 8 modeling plan hash:
  `823ee7a98be9a5199842536a65edd2394a39f1c5c6ae13947c29bd7c1c2494fe`.
- Phase 8 results hash:
  `d8471add338d79106fb1839008c5168535bb644505b5282a5e6236147b31255d`.
- Phase 9 plan file: `data/manifests/phase9_walkforward_plan.yaml`.
- Phase 9 plan hash:
  `4b1f0f0dd9f638ff4b5f40af04e17e8fc7753c4a500cac650d2537d3d40fb2c4`.
- The Phase 9 plan was created before Phase 9 result generation.
- Phase 9 result hash:
  `0e6567e9f67954df4ec5c74233f4e1d34759e4f6b7bf1f562be2e63997a44aee`.

Fold definitions:

- Eligible dates: the 24 first-of-month development dates from `2024-01-01`
  through `2025-12-01`.
- 2026 holdout access: `false`.
- Primary expanding window: 18 folds.
  Fold 1 trains `2024-01-01` through `2024-06-01`, validates
  `2024-07-01`.
  Final fold trains `2024-01-01` through `2025-11-01`, validates
  `2025-12-01`.
- Secondary rolling-6 diagnostic: 18 folds using the most recent 6 development
  dates strictly before each validation date.
- Every fold enforces `train_date < validation_date`.
- Deterministic anchor rule: `row_index % 10 == 0`, offset `0`.

Frozen models and features:

- QI direct baseline: fold-local linear baseline on `qi_1` with training-only
  median imputation and standardization.
- `lightgbm_qi_ofi`: LightGBM on `qi_1`, `ofi_1s`.
- `lightgbm_extended`: LightGBM on `qi_1`, `di_5`, `di_10`, `ofi_1s`,
  `trade_imbalance_1s`, `spread_bps`, `realized_vol_5s`, `mom_1s`,
  `book_update_count_1s`, `trade_count_1s`.
- LightGBM parameters were frozen from Phase 8:
  `n_estimators=120`, `learning_rate=0.05`, `num_leaves=15`, `max_depth=4`,
  `min_child_samples=200`, `subsample=0.8`, `colsample_bytree=0.9`,
  `reg_alpha=0.1`, `reg_lambda=1.0`, `random_state=8008`,
  `deterministic=true`, `force_col_wise=true`, `n_jobs=1`.
- No Phase 5 features, Phase 6 labels, model family, or hyperparameter was
  changed in response to Phase 9 results.

Primary expanding-window results:

- QI IC across 18 validation dates:
  mean `0.43185335860288265`, median `0.43736534099871366`, std
  `0.04983684910402971`, min `0.33959757606157515`, max
  `0.5083387678325725`, positive dates `18`, negative dates `0`.
- QI+OFI incremental IC vs QI:
  mean `0.006685448414447604`, median `0.006467151150689715`, std
  `0.004062299982712839`, min `-0.0005637752138100693`, max
  `0.014386104824419987`, positive folds `17`, negative folds `1`,
  zero folds `0`, fold-level t-stat `6.982240498110832`, exact sign-test
  p-value `0.00014495849609375`.
- Extended incremental IC vs QI:
  mean `0.010743286769766911`, median `0.010545310449919493`, std
  `0.004812944658168465`, min `0.004449872594214754`, max
  `0.021135799769847585`, positive folds `18`, negative folds `0`,
  zero folds `0`, fold-level t-stat `9.470274187642238`, exact sign-test
  p-value `7.62939453125e-06`.
- Extended incremental IC beyond QI+OFI:
  mean `0.004057838355319306`, median `0.0036061402317791036`, std
  `0.0024405027534758297`, min `0.0005799588117063048`, max
  `0.00984647371674685`, positive folds `18`, negative folds `0`,
  zero folds `0`, fold-level t-stat `7.054263750987976`, exact sign-test
  p-value `7.62939453125e-06`.

Calendar-period stability:

- 2024 H2 mean IC: QI `0.4510444425547074`, QI+OFI
  `0.4603107909440598`, Extended `0.46349081328996616`.
  Mean deltas: QI+OFI `0.009266348389352272`, Extended
  `0.012446370735258708`; positive-lift fraction `1.0` for both.
- 2025 H1 mean IC: QI `0.4234229895781818`, QI+OFI
  `0.4283998168539891`, Extended `0.43163868200094013`.
  Mean deltas: QI+OFI `0.004976827275807329`, Extended
  `0.0082156924227584`; positive-lift fraction `1.0` for both.
- 2025 H2 mean IC: QI `0.4210926436757589`, QI+OFI
  `0.42690581325394206`, Extended `0.43266044082704247`.
  Mean deltas: QI+OFI `0.005813169578183212`, Extended
  `0.011567797151283624`; positive-lift fraction `0.8333333333333334`
  for QI+OFI and `1.0` for Extended.

Expanding vs rolling-6 diagnostic:

- QI+OFI mean delta IC: expanding `0.006685448414447604`, rolling-6
  `0.006713687269404491`.
- Extended mean delta IC: expanding `0.010743286769766911`, rolling-6
  `0.00815920765565316`.
- Rolling-6 QI+OFI: positive folds `17`, negative folds `1`, t-stat
  `6.878562775893029`, sign-test p-value `0.00014495849609375`.
- Rolling-6 Extended: positive folds `18`, negative folds `0`, t-stat
  `7.259212276168416`, sign-test p-value `7.62939453125e-06`.
- Interpretation: QI+OFI lift is similar under rolling retraining, while
  Extended lift remains positive but smaller under rolling-6 than expanding.

Prediction similarity and model drift:

- Expanding Extended prediction vs QI rank correlation:
  mean `0.9537434074803967`, median `0.9545478244067476`, min
  `0.9336693815928105`, max `0.973759202036313`.
- Expanding QI+OFI prediction vs QI rank correlation:
  mean `0.9651497484494895`, median `0.9649553029245865`, min
  `0.954894841087616`, max `0.9778720768047318`.
- Expanding Extended prediction vs QI+OFI prediction rank correlation:
  mean `0.9855006994427798`, median `0.9866517006107787`, min
  `0.973340910709284`, max `0.9926761492878023`.
- Average expanding Extended LightGBM gain importance was led by `di_5`,
  `qi_1`, `trade_count_1s`, and `ofi_1s`.
- Correlated-feature importances are recorded for drift diagnostics only and
  are not interpreted causally.

Negative control:

- Control: deterministic permuted train target using the same expanding
  walk-forward machinery on folds `1`, `9`, and `18`, model
  `lightgbm_qi_ofi`.
- All three controls produced no rank signal with effectively constant
  predictions.
- Effective Spearman IC values: `0.0`, `0.0`, `0.0`.
- Mean effective Spearman IC: `0.0`.

Implementation and outputs:

- Added `src/microalpha/research/phase9.py`.
- Added `scripts/run_phase9_walkforward.py`.
- Added `tests/unit/test_phase9_walkforward.py`.
- Created compact tracked outputs under `reports/phase9/`:
  `walkforward_metrics.csv`, `incremental_lift.csv`,
  `window_comparison.csv`, `prediction_correlations.csv`,
  `feature_importance_by_fold.csv`, `negative_control.csv`,
  `phase9_summary.json`, `README.md`, and required figures.
- No row-level prediction files were written.

Exact local test results:

- `MPLCONFIGDIR=/tmp/microalpha-mpl PYTHONPATH=src /tmp/microalpha-phase8-venv/bin/python scripts/run_phase9_walkforward.py --clean`:
  PASS, result hash
  `0e6567e9f67954df4ec5c74233f4e1d34759e4f6b7bf1f562be2e63997a44aee`.
- Phase 9 result hash recomputation:
  PASS, matched stored hash
  `0e6567e9f67954df4ec5c74233f4e1d34759e4f6b7bf1f562be2e63997a44aee`.
- `python -m json.tool reports/phase9/phase9_summary.json`: PASS.
- `python -m pytest`: PASS, `135 passed, 49 warnings in 3.92s`.
- `ruff check src tests scripts`: PASS, `All checks passed!`.
- `python -m compileall -q src scripts tests`: PASS.
- `PATH=/tmp/microalpha-config-smoke-venv/bin:$PATH microalpha-smoke --manifest-out /tmp/microalpha-smoke.yaml`:
  PASS, config hash
  `8199bdda9ceea7571824b87d0fcd1927d457efb258075075a853f9dfb8885bd0`.
- Forbidden economic-word scan over Phase 9 artifacts and code: PASS, no
  matches.

Assumptions and limitations:

- Local default `python` is Python 3.10.9, not Python 3.11.
- Phase 9 LightGBM execution used the temporary environment at
  `/tmp/microalpha-phase8-venv` with working LightGBM dependencies.
- PyArrow emitted sandbox CPU-info warnings while reading parquet; these did
  not affect the Phase 9 gate.
- NumPy emitted warnings when negative-control predictions had no rank
  variation; those rows are explicitly marked
  `no_rank_signal_constant_prediction`.
- The accepted Phase 9 artifact state was later confirmed by GitHub Actions
  under Python 3.11 on commit
  `840465559903abf25857bf24a899202c2bbc9f47`.
- Phase 9 remains development-only temporal robustness analysis.
- No 2026 holdout date was read.
- No Phase 10 signal construction, execution simulation, cost analysis, or
  trading rule was implemented.

Next steps:

- Pre-Phase-10 GitHub Actions gate was cleared on exact Phase 9 commit
  `840465559903abf25857bf24a899202c2bbc9f47`.
- GitHub Actions `tests`: PASS, run `31353512353`.
- GitHub Actions `research-smoke`: PASS, run `31353512319`.
- Do not begin Phase 11 until the user explicitly accepts Phase 10 and requests
  continuation.

## Phase 10 - Signal Construction

Status: PASS locally

Pre-Phase-10 CI gate:

- Exact accepted Phase 9 artifact commit:
  `840465559903abf25857bf24a899202c2bbc9f47`.
- Remote `main` resolved to the same SHA before Phase 10 work began.
- GitHub Actions workflows use `actions/setup-python@v5` with
  `python-version: "3.11"`.
- GitHub Actions `tests` workflow: PASS.
  Run: `31353512353`.
  URL:
  `https://github.com/H2nryHe/Microstructure_Alpha_Execution_Lab/actions/runs/31353512353`.
  Head SHA:
  `840465559903abf25857bf24a899202c2bbc9f47`.
- GitHub Actions `research-smoke` workflow: PASS.
  Run: `31353512319`.
  URL:
  `https://github.com/H2nryHe/Microstructure_Alpha_Execution_Lab/actions/runs/31353512319`.
  Head SHA:
  `840465559903abf25857bf24a899202c2bbc9f47`.

Frozen inputs and plan:

- Phase 7 snapshot hash:
  `0bcdb7eddebbe83458998eff78844471afb78fc66d249a53aeb25667bebd803a`.
- Phase 7 result hash:
  `b86d51c4317f87d0cabf579f152d07c139e7fc23e47356d655bd09057342eb04`.
- Phase 7 audit hash:
  `b6b8206e03c81b47787d5ae4d4e5b960b4748bc75eed0ee5be4862ebf190d6e1`.
- Phase 8 modeling plan hash:
  `823ee7a98be9a5199842536a65edd2394a39f1c5c6ae13947c29bd7c1c2494fe`.
- Phase 8 result hash:
  `d8471add338d79106fb1839008c5168535bb644505b5282a5e6236147b31255d`.
- Phase 9 walk-forward plan hash:
  `4b1f0f0dd9f638ff4b5f40af04e17e8fc7753c4a500cac650d2537d3d40fb2c4`.
- Phase 9 result hash:
  `0e6567e9f67954df4ec5c74233f4e1d34759e4f6b7bf1f562be2e63997a44aee`.
- Phase 10 signal plan file: `data/manifests/phase10_signal_plan.yaml`.
- Phase 10 signal plan hash:
  `0ae8590cef7e7ea313c80889c74cc7db592a948f119e3982a2f2269df0c2a2bb`.
- The Phase 10 signal plan was created before signal evaluation.
- Phase 10 signal artifact hash:
  `68edd84a5ea6b72035976a0b0f48aabfc0183e17d6946fcbf69da7190f5de5d6`.
- Phase 10 results hash:
  `604a7b8a83990b9052c8fd329d93e93759a58d4b98960c2362f104a2d4b14f71`.

Signal rules:

- Eligible validation dates: `2024-07-01` through `2025-12-01`, matching the
  18 Phase 9 expanding-window validation folds.
- 2026 holdout access: `false`.
- Prediction source: regenerated Phase 9 expanding-window out-of-sample
  predictions.
- Model candidates: `qi_direct_baseline`, `lightgbm_qi_ofi`, and
  `lightgbm_extended`.
- Primary rule: `train_q10_q90`.
  Thresholds are estimated from each fold's training predictions only.
- Boundary behavior:
  LONG if `prediction >= training q90`;
  SHORT if `prediction <= training q10`;
  FLAT otherwise.
- Secondary diagnostics: `train_q05_q95` and `prediction_sign`.
- Non-finite predictions, invalid observations, stale observations, first row
  of each day, and last row of each day become FLAT deterministically.
- Generated signal values are limited to `-1`, `0`, and `1`.

Signal artifacts:

- Row-level signals were written outside Git under
  `/tmp/microalpha-phase10/signals`.
- Row-level signal artifact count: `54` Parquet files
  (`18` dates x `3` models).
- Row-level signal artifact size: approximately `133M`.
- Compact path-independent tracked manifest:
  `reports/phase10/signal_manifest.json`.
- Compact tracked outputs under `reports/phase10/`:
  `signal_summary.csv`, `signal_by_fold.csv`, `signal_transitions.csv`,
  `signal_persistence.csv`, `thresholds_by_fold.csv`,
  `model_signal_disagreement.csv`, `signal_future_mid_diagnostics.csv`,
  `signal_manifest.json`, `signal_trace_sample.csv`,
  `prediction_reconciliation.csv`, `phase10_summary.json`, `README.md`, and
  required figures.
- No row-level signal stream is committed to Git.

Prediction reconciliation and label isolation:

- Regenerated predictions reconciled against frozen Phase 9 compact metrics for
  all `54` fold/model rows.
- Reconciliation max absolute differences:
  Spearman IC `4.763412e-13`, prediction mean `4.852440e-18`,
  prediction std `4.996817e-17`.
- Reconciliation status: PASS.
- Label-mutation isolation test: PASS.
  Mutating future-return, future-move, and direction-label columns cannot
  change thresholds, raw signals, final signals, or signal artifact hash.
- Signal generation rejects future-derived columns before generating signals.

Threshold statistics:

- Mean q10 thresholds:
  `qi_direct_baseline=-2.2818582753209408e-05`,
  `lightgbm_qi_ofi=-2.3438808013423187e-05`,
  `lightgbm_extended=-2.2823068899197757e-05`.
- Mean q90 thresholds:
  `qi_direct_baseline=2.2888278677689708e-05`,
  `lightgbm_qi_ofi=2.357973672056846e-05`,
  `lightgbm_extended=2.2985208346034528e-05`.
- q05/q95 threshold drift is recorded in `thresholds_by_fold.csv` and figures.

Signal coverage:

- Primary 10/90 active coverage:
  `qi_direct_baseline=0.21705725829589728`,
  `lightgbm_qi_ofi=0.20213113613652564`,
  `lightgbm_extended=0.17317768411090395`.
- Primary long/short coverage:
  `qi_direct_baseline`: long `0.107837`, short `0.109221`;
  `lightgbm_qi_ofi`: long `0.100306`, short `0.101825`;
  `lightgbm_extended`: long `0.086459`, short `0.086719`.
- Secondary 5/95 active coverage:
  `qi_direct_baseline=0.118952`,
  `lightgbm_qi_ofi=0.108833`,
  `lightgbm_extended=0.086176`.
- Prediction-sign active coverage is effectively all non-boundary anchors for
  all three models and is diagnostic only.

Transition and churn statistics:

- Mean final-signal transition rate:
  `qi_direct_baseline=0.1770324626465384`,
  `lightgbm_qi_ofi=0.2449082571068742`,
  `lightgbm_extended=0.20987958358443803`.
- Mean direct reversal rate:
  `qi_direct_baseline=0.002918`,
  `lightgbm_qi_ofi=0.005511`,
  `lightgbm_extended=0.006195`.
- Mean raw/final signal changes are recorded in
  `reports/phase10/signal_transitions.csv`.

Persistence:

- Mean LONG run length in one-second anchors:
  `qi_direct_baseline=2.4828190838699595`,
  `lightgbm_qi_ofi=1.5967943501789448`,
  `lightgbm_extended=1.5488590518222731`.
- Mean SHORT run length in one-second anchors:
  `qi_direct_baseline=2.624652298438945`,
  `lightgbm_qi_ofi=1.6359806214472254`,
  `lightgbm_extended=1.559732257926976`.
- Median, p95, and maximum run lengths are recorded in
  `reports/phase10/signal_persistence.csv`.

Conditional future-mid diagnostics:

- Mean LONG-minus-SHORT future-mid return:
  `qi_direct_baseline=6.312122164443975e-05`,
  `lightgbm_qi_ofi=6.587916126813072e-05`,
  `lightgbm_extended=7.558446367242457e-05`.
- Mean signed future-mid effect:
  `qi_direct_baseline=3.156061082221988e-05`,
  `lightgbm_qi_ofi=3.293958063406536e-05`,
  `lightgbm_extended=3.779223183621228e-05`.
- Conditional LONG, FLAT, SHORT diagnostics are reported in
  `signal_future_mid_diagnostics.csv`.
- These are predictive signal-separation diagnostics only.

Model signal disagreement:

- Mean `qi_direct_baseline` vs `lightgbm_extended` disagreement fraction:
  `0.1330478193097956`.
- Mean `lightgbm_qi_ofi` vs `lightgbm_extended` disagreement fraction:
  `0.06632017530842774`.
- Directional disagreement breakdowns and descriptive future-mid outcomes are
  recorded in `model_signal_disagreement.csv`.

Exact local test results:

- `MPLCONFIGDIR=/tmp/microalpha-mpl PYTHONPATH=src /tmp/microalpha-phase8-venv/bin/python scripts/run_phase10_signals.py --clean`:
  PASS, signal artifact hash
  `68edd84a5ea6b72035976a0b0f48aabfc0183e17d6946fcbf69da7190f5de5d6`,
  result hash
  `604a7b8a83990b9052c8fd329d93e93759a58d4b98960c2362f104a2d4b14f71`.
- Phase 10 result hash recomputation:
  PASS, matched stored hash
  `604a7b8a83990b9052c8fd329d93e93759a58d4b98960c2362f104a2d4b14f71`.
- `python -m json.tool reports/phase10/phase10_summary.json`: PASS.
- `python -m json.tool reports/phase10/signal_manifest.json`: PASS.
- `python -m pytest`: PASS, `148 passed, 49 warnings in 4.17s`.
- `ruff check src tests scripts`: PASS, `All checks passed!`.
- `python -m compileall -q src scripts tests`: PASS.
- `PATH=/tmp/microalpha-config-smoke-venv/bin:$PATH microalpha-smoke --manifest-out /tmp/microalpha-smoke.yaml`:
  PASS, config hash
  `8199bdda9ceea7571824b87d0fcd1927d457efb258075075a853f9dfb8885bd0`.

Assumptions and limitations:

- Local default `python` is Python 3.10.9, not Python 3.11.
- Phase 10 LightGBM execution used the temporary environment at
  `/tmp/microalpha-phase8-venv` with working LightGBM dependencies.
- PyArrow emitted sandbox CPU-info warnings while reading parquet; these did
  not affect the Phase 10 gate.
- The exact Phase 10 artifact state was committed and pushed as
  `7b4bba3483bd6a7a3ae52acfd12bc91a830f6901`.
- GitHub Actions under Python 3.11 confirmed that exact Phase 10 commit:
  `tests` run `31391220465` PASS and `research-smoke` run `31391220513`
  PASS.
- Phase 10 signals are desired directional states only, not orders or fills.
- No 2026 holdout date was read.
- Phase 11 consumes Phase 10 signals as desired directional states and creates
  separate order/fill diagnostics.

Next steps:

- Phase 11 execution simulation has been completed below from the recorded
  Phase 10 CI-confirmed commit.

## Phase 11 - Event-Driven Execution Simulator

Status: PASS locally

Pre-Phase-11 gate:

- Exact accepted Phase 10 artifact commit:
  `7b4bba3483bd6a7a3ae52acfd12bc91a830f6901`.
- Remote `main` resolved to the same SHA before Phase 11 work began.
- GitHub Actions under Python 3.11 confirmed that exact commit:
  `tests` run `31391220465` PASS and `research-smoke` run `31391220513`
  PASS.

Frozen execution plan and config:

- Execution config file: `configs/execution.yaml`.
- Execution config hash:
  `7886f78e7552404f88ce446094353133a1590d22dd33ae1f3b647a3eb24132ef`.
- Phase 11 execution plan file:
  `data/manifests/phase11_execution_plan.yaml`.
- Phase 11 execution plan hash:
  `f5fa9ff916ef084cb1f7aa7d95f22058868ed39745aad14c27a0e2c2ee7d81a4`.
- The plan was frozen before Phase 11 real-data execution diagnostics.

Execution mechanics:

- Replay clock: `observation_time`.
- Exchange timestamps are retained for audit and not used to reorder replay.
- Same-timestamp tie policy: market states/trades at exactly the order-arrival
  timestamp are treated as already observed before the simulated order enters.
- Order sizing: fixed quote notional per unit signal transition,
  `target_order_notional_usd=10000.0`.
- Phase 10 final-signal state changes create orders; persistent signals do not
  submit another order every second.
- Market BUY orders consume asks from best ask upward.
- Market SELL orders consume bids from best bid downward.
- Displayed-depth shortfall is explicit; unavailable residual is not filled
  using inferred hidden liquidity.
- Passive BUY limit price is the best bid observable at order creation.
- Passive SELL limit price is the best ask observable at order creation.
- Passive queue approximation: displayed quantity at the limit price times
  `queue_fraction=1.0`.
- Book cancellations do not advance the simulated queue.
- Passive TTL: `1000ms`.
- Cancel latency: `100ms`.
- Real-data fee setting: `0.0 bps`; nonzero fee calculation is covered by
  synthetic tests.
- Markout horizons: `100ms`, `500ms`, `1000ms`, and `5000ms`.

Real-data scope:

- Instrument: `BTC-USDT`.
- Vendor symbol: Tardis/Binance `BTCUSDT`.
- Date used for MVP real-data mechanics diagnostics: `2024-07-01`.
- Models: `qi_direct_baseline`, `lightgbm_qi_ofi`, and `lightgbm_extended`.
- Latency scenarios: `0ms` and `100ms`.
- Book-state source:
  `/tmp/microalpha-multiday/derived/date=2024-07-01/research_100ms.parquet`.
- Passive queue-depletion trade source:
  `/tmp/microalpha-multiday/source/2024-07-01/BTCUSDT_trades.csv.gz`.
- Phase 10 signal source root: `/tmp/microalpha-phase10/signals`.
- Row-level Phase 11 artifact root: `/tmp/microalpha-phase11`.
- Row-level Phase 11 artifact size: about `38M`.
- Tracked compact report size: about `356K` under `reports/phase11`.
- All 54 Phase 10 signal artifact entries were checksum-verified before real
  execution diagnostics.

Artifacts and hashes:

- Phase 10 signal artifact hash verified:
  `68edd84a5ea6b72035976a0b0f48aabfc0183e17d6946fcbf69da7190f5de5d6`.
- Phase 11 execution artifact hash:
  `893c5196be53a00bcd5fb94362b60dece3da28aea2e264fe1f50bf6bbce415c0`.
- Phase 11 results hash:
  `a157c0eb1fb27043f19d6072b215645017d9cbc395b35047dd9b072d9d8ec2e0`.
- Phase 11 result hash recomputation matched the stored hash.

Compact reports:

- `reports/phase11/order_summary.csv`
- `reports/phase11/market_execution_summary.csv`
- `reports/phase11/passive_execution_summary.csv`
- `reports/phase11/fill_latency_summary.csv`
- `reports/phase11/depth_consumption_summary.csv`
- `reports/phase11/passive_fill_summary.csv`
- `reports/phase11/adverse_selection_summary.csv`
- `reports/phase11/runtime_summary.csv`
- `reports/phase11/execution_manifest.json`
- `reports/phase11/phase11_summary.json`
- `reports/phase11/README.md`
- Figures are under `reports/phase11/figures/`.

Order traffic:

- Market-order diagnostics generated `112988` orders.
- Passive-order diagnostics generated `112988` orders.
- Total fill child rows across market and passive diagnostics: `199392`.
- Orders per date/model/scenario:
  `qi_direct_baseline=14240`,
  `lightgbm_qi_ofi=21789`,
  `lightgbm_extended=20465`.
- Orders per active signal hour:
  `qi_direct_baseline=2882.2669515349153`,
  `lightgbm_qi_ofi=4300.696310104721`,
  `lightgbm_extended=4049.356930856326`.
- Orders per signal transition: `1.0`.

Market-order diagnostics:

- Market fill rate: `1.0` for all model/latency scenarios.
- Mean implementation shortfall versus decision mid:
  - `qi_direct_baseline`: `6.77987427046345e-06` at `0ms`,
    `1.0472018849959377e-05` at `100ms`.
  - `lightgbm_qi_ofi`: `5.1850238244400755e-06` at `0ms`,
    `8.28691755766168e-06` at `100ms`.
  - `lightgbm_extended`: `5.607924162474779e-06` at `0ms`,
    `9.781072581593323e-06` at `100ms`.
- Average levels consumed:
  `qi_direct_baseline=1.4127106741573034` at `0ms` and
  `1.365308988764045` at `100ms`;
  `lightgbm_qi_ofi=1.2925788241773373` at `0ms` and
  `1.2779384092890909` at `100ms`;
  `lightgbm_extended=1.322697288052773` at `0ms` and
  `1.30701197165893` at `100ms`.
- Market-order partial-fill counts from displayed depth:
  `qi_direct_baseline=61/61`,
  `lightgbm_qi_ofi=64/71`,
  `lightgbm_extended=65/74` for `0ms/100ms`.

Passive-order diagnostics:

- Passive fill rate:
  - `qi_direct_baseline`: `0.014747191011235955` at `0ms`,
    `0.02359550561797753` at `100ms`.
  - `lightgbm_qi_ofi`: `0.03639451099178485` at `0ms`,
    `0.04534398090779751` at `100ms`.
  - `lightgbm_extended`: `0.030930857561690693` at `0ms`,
    `0.03801612509161984` at `100ms`.
- Mean passive fill fraction:
  `qi_direct_baseline=0.013128770964257372/0.02116323707953792`,
  `lightgbm_qi_ofi=0.028111386227098304/0.03686223902300702`,
  `lightgbm_extended=0.023682415223913997/0.03036492513370315`
  for `0ms/100ms`.
- Median time-to-first-fill in milliseconds:
  `qi_direct_baseline=465.33950000000004/242.7215`,
  `lightgbm_qi_ofi=444.73699999999997/317.95799999999997`,
  `lightgbm_extended=445.35400000000004/346.01300000000003`
  for `0ms/100ms`.

Adverse-selection / markout diagnostics:

- Market-order average signed markouts at `100ms` were small negative across
  models, then positive at longer horizons in this diagnostic date.
- Passive-order average signed markouts were negative at all configured
  horizons in this diagnostic date.
- Full `100ms`, `500ms`, `1000ms`, and `5000ms` markouts are recorded in
  `reports/phase11/adverse_selection_summary.csv`.

Synthetic tests added:

- No fill before order arrival.
- Market BUY fills against asks, not bids.
- Market SELL fills against bids, not asks.
- Multi-level BUY and SELL depth consumption.
- Insufficient displayed depth leaves explicit residual.
- Passive queue depletion before simulated fill.
- Passive partial fill state.
- Expiration without forced fill.
- Cancel cannot remove causally earlier fills and prevents later fills after
  cancel effective time.
- Same-timestamp deterministic passive-fill tie rule.
- Exact fee calculation.
- BUY/SELL markout sign convention.
- Deterministic replay hash and future-mutation isolation.

Exact local verification:

- `python -m pytest`: PASS, `163 passed, 49 warnings in 3.20s`.
- `ruff check src tests scripts`: PASS, `All checks passed!`.
- `python -m compileall -q src scripts tests`: PASS.
- `PATH=/tmp/microalpha-config-smoke-venv/bin:$PATH microalpha-smoke --manifest-out /tmp/microalpha-smoke.yaml`:
  PASS, config hash
  `29d8157421a085a12a31c0f77c29b3b09f57cd2663c45513928815977eef1dd8`.
- `python -m json.tool reports/phase11/phase11_summary.json`: PASS.
- `python -m json.tool reports/phase11/execution_manifest.json`: PASS.
- Phase 11 result hash recomputation: PASS, matched
  `a157c0eb1fb27043f19d6072b215645017d9cbc395b35047dd9b072d9d8ec2e0`.
- `PYTHONPATH=src MPLCONFIGDIR=/tmp/microalpha-mpl python scripts/run_phase11_execution.py --clean`:
  PASS, with execution artifact hash
  `893c5196be53a00bcd5fb94362b60dece3da28aea2e264fe1f50bf6bbce415c0`
  and results hash
  `a157c0eb1fb27043f19d6072b215645017d9cbc395b35047dd9b072d9d8ec2e0`.

Assumptions and limitations:

- Local default `python` is Python 3.10.9, not Python 3.11.
- Phase 11 has not yet been committed, pushed, or confirmed in Python 3.11
  GitHub Actions.
- Real-data diagnostics are intentionally bounded to one development date,
  `2024-07-01`, for the Phase 11 MVP.
- Real-data book interaction uses existing `research_100ms.parquet` depth
  snapshots rather than replaying every incremental L2 update inside the
  execution loop.
- Passive queue depletion uses raw Tardis trade prints after order arrival, but
  exact exchange queue priority is unavailable.
- Real-data fee rate is set to `0.0 bps` to isolate execution mechanics; this
  is not a venue fee assumption.
- No 2026 holdout data was accessed.
- No portfolio cash balance, portfolio equity curve, Sharpe ratio, drawdown,
  cost grid, or accounting layer was implemented.

Next steps:

- Stop before Phase 12.
- Commit and push the exact Phase 11 artifact state only after user acceptance,
  then confirm Python 3.11 GitHub Actions on that exact commit before relying
  on CI portability.
