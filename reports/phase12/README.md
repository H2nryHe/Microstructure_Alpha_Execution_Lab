# Phase 12 Accounting Reports

Phase 12 consumes the frozen Phase 11 fills and builds isolated
self-financing ledgers. Each scenario starts with zero cash and zero inventory,
uses signed BTC quantities, marks inventory to the most recent observable
100ms mid price, and does not add a terminal liquidation fill.

- Phase 12 accounting plan hash: `a43f49a5d99393cc26b76e86628e67c4459a215f2eb5ad3a241dd339ee3094a9`
- Phase 11 execution artifact hash: `893c5196be53a00bcd5fb94362b60dece3da28aea2e264fe1f50bf6bbce415c0`
- Full ledger artifact root: `/tmp/microalpha-phase12`

The current real-data accounting scope is one development date,
`2024-07-01`. No annualized metrics, Sharpe ratio, or Phase 13 cost/latency
sweep is reported.
