# Phase 11 Execution Diagnostics

Phase 11 converts frozen Phase 10 desired directional states into deterministic
market and passive order/fill diagnostics. The run uses observation-time replay
ordering, explicit order-arrival timestamps, fixed quote-notional order sizing,
and no portfolio accounting.

- Execution plan hash: `f5fa9ff916ef084cb1f7aa7d95f22058868ed39745aad14c27a0e2c2ee7d81a4`
- Execution config hash: `7886f78e7552404f88ce446094353133a1590d22dd33ae1f3b647a3eb24132ef`
- Phase 10 signal artifact hash: `68edd84a5ea6b72035976a0b0f48aabfc0183e17d6946fcbf69da7190f5de5d6`
- Real-data dates: `2024-07-01`
- Row-level artifact root: `/tmp/microalpha-phase11`

The real-data MVP uses `research_100ms.parquet` depth snapshots for book state
and Tardis raw trade prints for passive queue depletion. Markouts are computed
only after fills are frozen.
