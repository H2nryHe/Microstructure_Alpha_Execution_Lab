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

## CI / Config Hardening

Status: PASS locally; Python 3.11 GitHub Actions confirmation pending

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

Assumptions and risks:

- Local smoke verification used Python with PyYAML but not Python 3.11 because
  no `python3.11` binary is installed in this workspace.
- Python 3.11+ compatibility and the GitHub Actions `research-smoke` result
  must be confirmed from CI after the fix is pushed.
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

- Stop before Phase 5 until the user accepts Phase 4 or requests continuation.
