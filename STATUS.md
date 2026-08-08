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
