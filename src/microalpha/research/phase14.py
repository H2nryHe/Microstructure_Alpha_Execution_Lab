"""Phase 14 execution and economic robustness helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

from microalpha.accounting.ledger import Fill, process_fill
from microalpha.utils.hashing import hash_config

PHASE14_ROBUSTNESS_PLAN_HASH = "a0315262cb252c9e8b0bb0d63891e92cdfe5d16d0d7924cc570dc49b64107317"
PHASE13_COMMIT_SHA = "ebfbee1a0c06c5a908e760ce8015346e66f35295"
PHASE13_EXECUTION_GRID_ARTIFACT_HASH = (
    "45ada7b581b9e5240661b2fc5bdb3e137f8a5e86674fa9563685104f10eda5cb"
)
PHASE13_RESULTS_HASH = "3798edf860c8a493d17fcbbe201b6fd5a2e61a10ab955d504ee160bfdffef990"

PRIMARY_MARKET_DATES = [
    "2024-07-01",
    "2024-10-01",
    "2025-01-01",
    "2025-04-01",
    "2025-07-01",
    "2025-10-01",
]
PASSIVE_DATES = ["2024-07-01", "2025-01-01", "2025-07-01"]
SENSITIVITY_DATES = ["2024-07-01", "2025-01-01", "2025-07-01"]
QUEUE_TTL_DATES = ["2024-07-01", "2025-07-01"]
MODELS = ["qi_direct_baseline", "lightgbm_qi_ofi", "lightgbm_extended"]
MARKET_LATENCIES_MS = [0, 100]
MARKET_FEE_BPS = [0.0, 0.25, 0.5]
ORDER_SIZE_NOTIONAL_USD = [1000.0, 10000.0, 50000.0]
QUEUE_FRACTIONS = [0.5, 1.0]
TTL_MS = [500, 1000, 2000]
TERMINAL_MARK_SHOCK_BPS = [-10.0, -5.0, 0.0, 5.0, 10.0]
FORBIDDEN_HOLDOUT_YEAR = 2026
REPORT_FILES = [
    "market_multiday_results.csv",
    "market_date_level_summary.csv",
    "market_breakeven_by_date.csv",
    "model_ranking_stability.csv",
    "incremental_economics_by_date.csv",
    "latency_robustness.csv",
    "order_size_sensitivity.csv",
    "passive_multiday_results.csv",
    "passive_queue_sensitivity.csv",
    "passive_ttl_sensitivity.csv",
    "passive_inventory_stress.csv",
    "robustness_manifest.json",
    "README.md",
]


@dataclass(frozen=True)
class FastAccountingResult:
    gross_pnl: float
    realized_pnl: float
    terminal_unrealized_pnl: float
    terminal_position: float
    terminal_inventory_value: float
    max_abs_position: float
    turnover: float
    buy_notional: float
    sell_notional: float


def assert_exact_dates(actual: Iterable[str], expected: Iterable[str], label: str) -> None:
    actual_list = list(actual)
    expected_list = list(expected)
    if actual_list != expected_list:
        raise ValueError(f"{label} dates are not the frozen mechanical selection")
    if any(str(date).startswith(f"{FORBIDDEN_HOLDOUT_YEAR}-") for date in actual_list):
        raise ValueError(f"{label} contains forbidden holdout-year date")


def fee_quote(turnover: float, fee_bps: float) -> float:
    return float(turnover) * float(fee_bps) / 10_000.0


def breakeven_fee_bps(gross_pnl: float, turnover: float) -> float | None:
    if turnover <= 0 or gross_pnl < 0:
        return None
    return float(gross_pnl) / float(turnover) * 10_000.0


def fast_accounting_from_fills(
    fills: list[Fill],
    *,
    terminal_mark_mid: float,
) -> FastAccountingResult:
    state = {
        "position": 0.0,
        "average_entry_price": 0.0,
        "realized_pnl": 0.0,
        "gross_cash": 0.0,
        "net_cash": 0.0,
        "fees_paid": 0.0,
        "turnover": 0.0,
        "buy_notional": 0.0,
        "sell_notional": 0.0,
        "gross_traded_quantity": 0.0,
    }
    max_abs_position = 0.0
    for fill in sorted(fills, key=lambda item: (item.fill_time, item.order_id, item.child_index)):
        state = process_fill(fill=fill, **state)
        max_abs_position = max(max_abs_position, abs(state["position"]))
    terminal_position = state["position"]
    terminal_inventory_value = terminal_position * float(terminal_mark_mid)
    gross_pnl = state["gross_cash"] + terminal_inventory_value
    if terminal_position > 0:
        unrealized = (float(terminal_mark_mid) - state["average_entry_price"]) * abs(
            terminal_position
        )
    elif terminal_position < 0:
        unrealized = (state["average_entry_price"] - float(terminal_mark_mid)) * abs(
            terminal_position
        )
    else:
        unrealized = 0.0
    return FastAccountingResult(
        gross_pnl=float(gross_pnl),
        realized_pnl=float(state["realized_pnl"]),
        terminal_unrealized_pnl=float(unrealized),
        terminal_position=float(terminal_position),
        terminal_inventory_value=float(terminal_inventory_value),
        max_abs_position=float(max_abs_position),
        turnover=float(state["turnover"]),
        buy_notional=float(state["buy_notional"]),
        sell_notional=float(state["sell_notional"]),
    )


def terminal_inventory_stress(
    *,
    terminal_position: float,
    terminal_mark_mid: float,
    base_equity: float,
    shock_bps: float,
) -> tuple[float, float]:
    equity_delta = float(terminal_position) * float(terminal_mark_mid) * float(shock_bps) / 10_000.0
    return equity_delta, float(base_equity) + equity_delta


def date_level_summary(rows: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    grouped = rows.groupby(group_cols, dropna=False)
    return grouped.agg(
        mean_daily_gross_bps=("gross_pnl_bps_of_turnover", "mean"),
        median_daily_gross_bps=("gross_pnl_bps_of_turnover", "median"),
        min_daily_gross_bps=("gross_pnl_bps_of_turnover", "min"),
        max_daily_gross_bps=("gross_pnl_bps_of_turnover", "max"),
        positive_gross_days=("gross_pnl", lambda values: int((values > 0).sum())),
        negative_gross_days=("gross_pnl", lambda values: int((values < 0).sum())),
        positive_net_days=("net_pnl", lambda values: int((values > 0).sum())),
        negative_net_days=("net_pnl", lambda values: int((values < 0).sum())),
    ).reset_index()


def ranking_counts(
    rows: pd.DataFrame,
    *,
    value_column: str,
    rank_context_cols: list[str],
    metric_label: str | None = None,
) -> pd.DataFrame:
    metric = metric_label or value_column
    winners = []
    for keys, group in rows.groupby(rank_context_cols, dropna=False):
        max_value = group[value_column].max()
        winning = group[group[value_column] == max_value]
        key_values = keys if isinstance(keys, tuple) else (keys,)
        for row in winning.itertuples(index=False):
            winners.append(
                {
                    **dict(zip(rank_context_cols, key_values, strict=True)),
                    "metric": metric,
                    "model": row.model,
                    "rank_first_share": 1.0 / len(winning),
                }
            )
    return (
        pd.DataFrame(winners)
        .groupby(["metric", "model"], dropna=False)["rank_first_share"]
        .sum()
        .reset_index(name="first_place_count")
        .sort_values(["metric", "model"])
    )


def ensure_negative_days_retained(rows: pd.DataFrame) -> None:
    if "gross_pnl" in rows and not (rows["gross_pnl"] < 0).any():
        raise ValueError("Robustness output has no retained negative gross PnL rows")


def deterministic_sort(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    return frame.sort_values(columns).reset_index(drop=True)


def phase14_results_hash(output_dir: Path, summary_without_hash: dict[str, object]) -> str:
    payload: dict[str, object] = {
        "phase14_summary_without_results_hash": summary_without_hash
    }
    for filename in REPORT_FILES:
        payload[filename] = (output_dir / filename).read_text(encoding="utf-8")
    return hash_config(payload)
