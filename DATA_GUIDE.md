# Data Guide

## Instrument Mapping

- Internal instrument: `BTC-USDT`
- Binance/Tardis vendor symbol: `BTCUSDT`

Vendor-specific parsing is isolated behind ingestion adapters. Downstream raw,
bronze, research, feature, label, signal, execution, and accounting schemas use
project-level names rather than assuming a single vendor schema.

## Data Families

- Trades: aggressive trade prints with price, amount, side, and source
  timestamps.
- Incremental L2: order-book level updates suitable for replay and BBO/depth
  reconstruction.

Binance official public historical Spot trades are used for real trade
ingestion validation. Binance's standard public historical archive is not
treated as a historical Spot incremental-L2 source. Tardis normalized Binance
Spot data is the MVP source for historical incremental L2 replay validation.

## Raw Data Policy

Large vendor datasets are not committed to Git. The repository keeps only small
regression fixtures and curated reports/figures. Full raw files should live in
local work directories, be copied byte-for-byte into the raw layer during
ingestion, and remain immutable after checksum verification.

## Timestamp Semantics

For Tardis normalized L2/trades, `timestamp` and `local_timestamp` are
microseconds since Unix epoch in UTC. Local/receive observation time governs
causal replay and feature eligibility; exchange timestamp is retained for audit.
For Binance Spot trade archives, timestamp units are documented by source/date
and are not inferred purely from column names.

## Checksums and Manifests

Ingestion computes project SHA-256 checksums and preserves vendor checksums when
available. Manifests record source identity, instrument mapping, timestamp
units, raw/bronze artifact paths, schema assumptions, and row counts. Later
research phases record deterministic config and artifact hashes so public
results can be traced back to source decisions.
