"""Run Phase 14 multi-date execution and economic robustness diagnostics."""

from __future__ import annotations

import argparse
import copy
import json
import shutil
from pathlib import Path
from typing import Any

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from microalpha.accounting.ledger import Fill
from microalpha.config import load_yaml_config
from microalpha.research.phase10 import signal_manifest_hash
from microalpha.research.phase14 import (
    FORBIDDEN_HOLDOUT_YEAR,
    MARKET_FEE_BPS,
    MARKET_LATENCIES_MS,
    MODELS,
    ORDER_SIZE_NOTIONAL_USD,
    PASSIVE_DATES,
    PHASE13_COMMIT_SHA,
    PHASE13_EXECUTION_GRID_ARTIFACT_HASH,
    PHASE13_RESULTS_HASH,
    PHASE14_ROBUSTNESS_PLAN_HASH,
    PRIMARY_MARKET_DATES,
    QUEUE_FRACTIONS,
    QUEUE_TTL_DATES,
    SENSITIVITY_DATES,
    TERMINAL_MARK_SHOCK_BPS,
    TTL_MS,
    assert_exact_dates,
    breakeven_fee_bps,
    date_level_summary,
    deterministic_sort,
    ensure_negative_days_retained,
    fast_accounting_from_fills,
    fee_quote,
    phase14_results_hash,
    ranking_counts,
    terminal_inventory_stress,
)
from microalpha.utils.hashing import hash_config
from run_phase11_execution import (
    PHASE10_SIGNAL_ARTIFACT_HASH,
    PHASE11_EXECUTION_CONFIG_HASH,
    PHASE11_EXECUTION_PLAN_HASH,
    read_book_frame,
    read_trades,
    run_scenario,
    sha256_file,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", default="data/manifests/phase14_robustness_plan.yaml")
    parser.add_argument("--execution-config", default="configs/execution.yaml")
    parser.add_argument("--phase10-manifest", default="reports/phase10/signal_manifest.json")
    parser.add_argument("--phase13-summary", default="reports/phase13/phase13_summary.json")
    parser.add_argument("--artifact-root", default="/tmp/microalpha-phase14-execution")
    parser.add_argument("--output-dir", default="reports/phase14")
    parser.add_argument("--clean", action="store_true")
    return parser.parse_args()


def verify_inputs(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    plan_hash = hash_config(load_yaml_config(args.plan))
    if plan_hash != PHASE14_ROBUSTNESS_PLAN_HASH:
        raise ValueError(f"Phase 14 plan hash mismatch: {plan_hash}")
    assert_exact_dates(PRIMARY_MARKET_DATES, PRIMARY_MARKET_DATES, "primary")
    assert_exact_dates(PASSIVE_DATES, PASSIVE_DATES, "passive")
    phase13 = json.loads(Path(args.phase13_summary).read_text(encoding="utf-8"))
    if phase13["phase13_execution_grid_artifact_hash"] != PHASE13_EXECUTION_GRID_ARTIFACT_HASH:
        raise ValueError("Phase 13 execution-grid artifact hash mismatch")
    if phase13["phase13_results_hash"] != PHASE13_RESULTS_HASH:
        raise ValueError("Phase 13 results hash mismatch")

    signal_manifest = json.loads(Path(args.phase10_manifest).read_text(encoding="utf-8"))
    payload = {
        key: value
        for key, value in signal_manifest.items()
        if key != "phase10_signal_artifact_hash"
    }
    if signal_manifest_hash(payload) != PHASE10_SIGNAL_ARTIFACT_HASH:
        raise ValueError("Phase 10 signal manifest hash mismatch")
    signal_root = Path(config["real_data_diagnostic"]["signal_root"])
    required_dates = sorted(set(PRIMARY_MARKET_DATES + PASSIVE_DATES + SENSITIVITY_DATES))
    verified_signals = 0
    manifest_by_relative = {
        entry["relative_artifact_id"]: entry for entry in signal_manifest["entries"]
    }
    for date in required_dates:
        if date.startswith(f"{FORBIDDEN_HOLDOUT_YEAR}-"):
            raise ValueError("Phase 14 must not access holdout-year dates")
        for model in MODELS:
            relative = f"date={date}/model={model}/signals.parquet"
            if relative not in manifest_by_relative:
                raise ValueError(f"Missing Phase 10 signal manifest entry: {relative}")
            path = signal_root / relative
            if sha256_file(path) != manifest_by_relative[relative]["sha256"]:
                raise ValueError(f"Phase 10 signal checksum mismatch: {relative}")
            verified_signals += 1

    return {
        "phase13_commit_sha": PHASE13_COMMIT_SHA,
        "phase13_tests_run_id": 31405386682,
        "phase13_research_smoke_run_id": 31405386687,
        "phase13_execution_grid_artifact_hash": PHASE13_EXECUTION_GRID_ARTIFACT_HASH,
        "phase13_results_hash": PHASE13_RESULTS_HASH,
        "phase14_plan_hash": PHASE14_ROBUSTNESS_PLAN_HASH,
        "phase10_signal_artifact_hash": PHASE10_SIGNAL_ARTIFACT_HASH,
        "phase10_signal_entries_verified_for_phase14": verified_signals,
        "phase11_execution_plan_hash": PHASE11_EXECUTION_PLAN_HASH,
        "phase11_execution_config_hash": PHASE11_EXECUTION_CONFIG_HASH,
    }


def scenario_config(
    config: dict[str, Any],
    *,
    dates: list[str],
    models: list[str],
    order_notional: float = 10000.0,
    queue_fraction: float = 1.0,
    ttl_ms: int = 1000,
) -> dict[str, Any]:
    value = copy.deepcopy(config)
    value["real_data_diagnostic"]["dates"] = dates
    value["real_data_diagnostic"]["models"] = models
    value["order_sizing"]["target_order_notional_usd"] = float(order_notional)
    value["passive_orders"]["queue_fraction"] = float(queue_fraction)
    value["passive_orders"]["limit_ttl_ms"] = int(ttl_ms)
    value["fees"]["primary_real_data_fee_bps"] = 0.0
    return value


def run_grid(
    *,
    config: dict[str, Any],
    dates: list[str],
    models: list[str],
    mode: str,
    latencies_ms: list[int],
    output_root: Path,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    depth = int(config["market_orders"]["max_book_depth_levels"])
    derived_root = Path(config["real_data_diagnostic"]["derived_root"])
    source_root = Path(config["real_data_diagnostic"]["source_root"])
    for date in dates:
        if date.startswith(f"{FORBIDDEN_HOLDOUT_YEAR}-"):
            raise ValueError("Phase 14 must not access holdout-year dates")
        book_frame, book_times = read_book_frame(derived_root, date, depth)
        trades, trade_times = read_trades(source_root, date)
        for model in models:
            signal_path = (
                Path(config["real_data_diagnostic"]["signal_root"])
                / f"date={date}"
                / f"model={model}"
                / "signals.parquet"
            )
            signal_hash_before = sha256_file(signal_path)
            for latency_ms in latencies_ms:
                entry = run_scenario(
                    date=date,
                    model=model,
                    mode=mode,
                    latency_ms=latency_ms,
                    config=config,
                    book_frame=book_frame,
                    book_times=book_times,
                    trades=trades,
                    trade_times=trade_times,
                    output_root=output_root,
                )
                if sha256_file(signal_path) != signal_hash_before:
                    raise ValueError(f"Phase 10 signal artifact mutated: {signal_path}")
                entries.append(entry)
    return entries


def terminal_mark(derived_root: Path, date: str) -> float:
    if date.startswith(f"{FORBIDDEN_HOLDOUT_YEAR}-"):
        raise ValueError("Phase 14 must not access holdout-year dates")
    path = derived_root / f"date={date}" / "research_100ms.parquet"
    frame = pd.read_parquet(path, columns=["mid"])
    return float(pd.to_numeric(frame["mid"], errors="coerce").dropna().iloc[-1])


def fill_records(fills: pd.DataFrame) -> list[Fill]:
    frame = fills.reset_index(drop=True).copy()
    frame["child_index"] = frame.groupby("order_id").cumcount()
    frame["fill_id"] = [
        f"{row.order_id}:{int(row.child_index)}" for row in frame.itertuples(index=False)
    ]
    records = []
    for row in frame.itertuples(index=False):
        records.append(
            Fill(
                fill_id=str(row.fill_id),
                order_id=str(row.order_id),
                fill_time=pd.Timestamp(row.fill_time).to_pydatetime(),
                side=str(row.side),
                price=float(row.price),
                quantity=float(row.quantity),
                signed_quantity=float(row.signed_quantity),
                fee_quote=0.0,
                child_index=int(row.child_index),
            )
        )
    return records


def base_metrics(
    *,
    entry: dict[str, Any],
    artifact_root: Path,
    derived_root: Path,
    scenario_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base = artifact_root / entry["relative_dir"]
    orders = pd.read_parquet(base / "orders.parquet")
    fills = pd.read_parquet(base / "fills.parquet")
    markouts = pd.read_parquet(base / "markouts.parquet")
    accounting = fast_accounting_from_fills(
        fill_records(fills),
        terminal_mark_mid=terminal_mark(derived_root, str(entry["date"])),
    )
    order_count = int(len(orders))
    filled_parent_orders = int((orders["filled_quantity"].astype(float) > 0).sum())
    fill_count = int(len(fills))
    common = {
        "date": entry["date"],
        "model": entry["model"],
        "mode": entry["mode"],
        "latency_ms": int(entry["latency_ms"]),
        "parent_orders": order_count,
        "fills": fill_count,
        "filled_parent_orders": filled_parent_orders,
        "fill_rate": 0.0 if order_count == 0 else filled_parent_orders / order_count,
        "turnover": accounting.turnover,
        "gross_pnl": accounting.gross_pnl,
        "gross_pnl_bps_of_turnover": 0.0
        if accounting.turnover == 0
        else accounting.gross_pnl / accounting.turnover * 10000.0,
        "terminal_position": accounting.terminal_position,
        "terminal_notional_exposure": accounting.terminal_inventory_value,
        "max_inventory": accounting.max_abs_position,
        "mean_implementation_shortfall": float(
            fills["implementation_shortfall_vs_decision_mid"].mean()
        )
        if not fills.empty
        else np.nan,
        "mean_levels_consumed": float(orders["levels_consumed"].mean())
        if "levels_consumed" in orders
        else np.nan,
        "full_fills": int((orders["status"] == "FILLED").sum()) if "status" in orders else 0,
        "partial_fills": int((orders["status"] == "PARTIALLY_FILLED").sum())
        if "status" in orders
        else 0,
        "expired_or_no_fill": int((orders["filled_quantity"].astype(float) <= 0).sum()),
        "mean_fill_fraction": float(orders["fill_fraction"].mean())
        if "fill_fraction" in orders
        else np.nan,
        "maker_fill_count": int((fills["liquidity_role"] == "maker").sum())
        if not fills.empty and "liquidity_role" in fills
        else 0,
        "taker_on_arrival_count": int(
            (fills["liquidity_role"] == "taker_marketable_limit").sum()
        )
        if not fills.empty and "liquidity_role" in fills
        else 0,
    }
    for horizon in [100, 500, 1000, 5000]:
        column = f"signed_markout_{horizon}ms"
        common[f"average_signed_markout_{horizon}ms"] = (
            float(markouts[column].mean()) if column in markouts and not markouts.empty else np.nan
        )
    if scenario_extra:
        common.update(scenario_extra)
    return common


def market_fee_rows(base_rows: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for scenario in base_rows.itertuples(index=False):
        for fee_bps in MARKET_FEE_BPS:
            fee = fee_quote(scenario.turnover, fee_bps)
            rows.append(
                {
                    **scenario._asdict(),
                    "fee_bps": fee_bps,
                    "fee_quote": fee,
                    "net_pnl": scenario.gross_pnl - fee,
                    "net_pnl_bps_of_turnover": 0.0
                    if scenario.turnover == 0
                    else (scenario.gross_pnl - fee) / scenario.turnover * 10000.0,
                }
            )
    return deterministic_sort(pd.DataFrame(rows), ["date", "model", "latency_ms", "fee_bps"])


def breakeven_report(market_base: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for row in market_base.itertuples(index=False):
        be = breakeven_fee_bps(row.gross_pnl, row.turnover)
        rows.append(
            {
                "date": row.date,
                "model": row.model,
                "latency_ms": row.latency_ms,
                "gross_pnl": row.gross_pnl,
                "turnover": row.turnover,
                "breakeven_fee_bps": be,
                "breakeven_fee_status": "DEFINED" if be is not None else "ALREADY_NEGATIVE",
                "breakeven_gt_0_10bps": bool(be is not None and be > 0.10),
                "breakeven_gt_0_25bps": bool(be is not None and be > 0.25),
                "breakeven_gt_0_50bps": bool(be is not None and be > 0.50),
            }
        )
    frame = deterministic_sort(pd.DataFrame(rows), ["date", "model", "latency_ms"])
    summary = (
        frame.groupby(["model", "latency_ms"], dropna=False)
        .agg(
            mean_breakeven_fee_bps=("breakeven_fee_bps", "mean"),
            median_breakeven_fee_bps=("breakeven_fee_bps", "median"),
            min_breakeven_fee_bps=("breakeven_fee_bps", "min"),
            max_breakeven_fee_bps=("breakeven_fee_bps", "max"),
            fraction_dates_gt_0_10bps=("breakeven_gt_0_10bps", "mean"),
            fraction_dates_gt_0_25bps=("breakeven_gt_0_25bps", "mean"),
            fraction_dates_gt_0_50bps=("breakeven_gt_0_50bps", "mean"),
        )
        .reset_index()
    )
    return frame.merge(summary, on=["model", "latency_ms"], how="left")


def ranking_report(market_rows: pd.DataFrame) -> pd.DataFrame:
    ranking_frames = [
        ranking_counts(
            market_rows[market_rows["fee_bps"] == 0.0],
            value_column="gross_pnl",
            rank_context_cols=["date", "latency_ms", "fee_bps"],
        ),
        ranking_counts(
            market_rows[market_rows["fee_bps"] == 0.0],
            value_column="gross_pnl_bps_of_turnover",
            rank_context_cols=["date", "latency_ms", "fee_bps"],
        ),
        ranking_counts(
            market_rows[market_rows["fee_bps"] == 0.25],
            value_column="net_pnl",
            rank_context_cols=["date", "latency_ms", "fee_bps"],
            metric_label="net_pnl_0_25bps_fee",
        ),
        ranking_counts(
            market_rows[market_rows["fee_bps"] == 0.5],
            value_column="net_pnl",
            rank_context_cols=["date", "latency_ms", "fee_bps"],
            metric_label="net_pnl_0_50bps_fee",
        ),
    ]
    return pd.concat(ranking_frames, ignore_index=True)


def incremental_report(market_rows: pd.DataFrame) -> pd.DataFrame:
    rows = []
    comparisons = [
        ("lightgbm_extended", "qi_direct_baseline", "Extended_minus_QI"),
        ("lightgbm_qi_ofi", "qi_direct_baseline", "QI_OFI_minus_QI"),
        ("lightgbm_extended", "lightgbm_qi_ofi", "Extended_minus_QI_OFI"),
    ]
    for date in PRIMARY_MARKET_DATES:
        for latency in MARKET_LATENCIES_MS:
            zero = market_rows[
                (market_rows["date"] == date)
                & (market_rows["latency_ms"] == latency)
                & (market_rows["fee_bps"] == 0.0)
            ]
            for left, right, label in comparisons:
                left_zero = zero[zero["model"] == left].iloc[0]
                right_zero = zero[zero["model"] == right].iloc[0]
                for fee_bps in [0.25, 0.5]:
                    fee_slice = market_rows[
                        (market_rows["date"] == date)
                        & (market_rows["latency_ms"] == latency)
                        & (market_rows["fee_bps"] == fee_bps)
                    ]
                    left_fee = fee_slice[fee_slice["model"] == left].iloc[0]
                    right_fee = fee_slice[fee_slice["model"] == right].iloc[0]
                    rows.append(
                        {
                            "date": date,
                            "latency_ms": latency,
                            "comparison": label,
                            "left_model": left,
                            "right_model": right,
                            "fee_bps": fee_bps,
                            "delta_gross_pnl": left_zero.gross_pnl - right_zero.gross_pnl,
                            "delta_turnover": left_zero.turnover - right_zero.turnover,
                            "delta_gross_bps_per_turnover": left_zero.gross_pnl_bps_of_turnover
                            - right_zero.gross_pnl_bps_of_turnover,
                            "delta_net_pnl": left_fee.net_pnl - right_fee.net_pnl,
                        }
                    )
    return deterministic_sort(pd.DataFrame(rows), ["date", "latency_ms", "comparison", "fee_bps"])


def latency_report(market_base: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for date in PRIMARY_MARKET_DATES:
        for model in MODELS:
            zero = market_base[
                (market_base["date"] == date)
                & (market_base["model"] == model)
                & (market_base["latency_ms"] == 0)
            ].iloc[0]
            delayed = market_base[
                (market_base["date"] == date)
                & (market_base["model"] == model)
                & (market_base["latency_ms"] == 100)
            ].iloc[0]
            rows.append(
                {
                    "date": date,
                    "model": model,
                    "gross_pnl_0ms": zero.gross_pnl,
                    "gross_pnl_100ms": delayed.gross_pnl,
                    "gross_pnl_erosion": zero.gross_pnl - delayed.gross_pnl,
                    "gross_bps_0ms": zero.gross_pnl_bps_of_turnover,
                    "gross_bps_100ms": delayed.gross_pnl_bps_of_turnover,
                    "gross_bps_erosion": zero.gross_pnl_bps_of_turnover
                    - delayed.gross_pnl_bps_of_turnover,
                    "latency_worsened": delayed.gross_pnl < zero.gross_pnl,
                    "latency_improved": delayed.gross_pnl > zero.gross_pnl,
                }
            )
    return deterministic_sort(pd.DataFrame(rows), ["date", "model"])


def passive_inventory_stress(passive: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for scenario in passive.itertuples(index=False):
        for shock_bps in TERMINAL_MARK_SHOCK_BPS:
            delta, shocked = terminal_inventory_stress(
                terminal_position=scenario.terminal_position,
                terminal_mark_mid=scenario.terminal_notional_exposure
                / scenario.terminal_position
                if abs(scenario.terminal_position) > 1e-12
                else 0.0,
                base_equity=scenario.gross_pnl,
                shock_bps=shock_bps,
            )
            rows.append(
                {
                    "date": scenario.date,
                    "model": scenario.model,
                    "latency_ms": scenario.latency_ms,
                    "terminal_position": scenario.terminal_position,
                    "terminal_notional_exposure": scenario.terminal_notional_exposure,
                    "terminal_mark_shock_bps": shock_bps,
                    "terminal_equity_delta": delta,
                    "shocked_terminal_equity": shocked,
                }
            )
    return deterministic_sort(
        pd.DataFrame(rows),
        ["date", "model", "latency_ms", "terminal_mark_shock_bps"],
    )


def write_figures(output_dir: Path, frames: dict[str, pd.DataFrame]) -> None:
    figures = output_dir / "figures"
    figures.mkdir(parents=True, exist_ok=True)

    def save(name: str) -> None:
        plt.tight_layout()
        plt.savefig(figures / name, dpi=140)
        plt.close()

    market = frames["market_multiday_results.csv"]
    market0 = market[(market["fee_bps"] == 0.0) & (market["latency_ms"] == 0)]
    for model, group in market0.groupby("model"):
        plt.plot(group["date"], group["gross_pnl_bps_of_turnover"], marker="o", label=model)
    plt.xticks(rotation=45, ha="right")
    plt.axhline(0, color="black", linewidth=0.8)
    plt.legend()
    plt.title("Market gross bps/turnover by date and model")
    save("market_gross_bps_by_date_model.png")

    net_fee_figures = [
        (0.25, "market_net_bps_025_by_date.png"),
        (0.5, "market_net_bps_050_by_date.png"),
    ]
    for fee, name in net_fee_figures:
        subset = market[(market["fee_bps"] == fee) & (market["latency_ms"] == 0)]
        for model, group in subset.groupby("model"):
            plt.plot(group["date"], group["net_pnl_bps_of_turnover"], marker="o", label=model)
        plt.xticks(rotation=45, ha="right")
        plt.axhline(0, color="black", linewidth=0.8)
        plt.legend()
        plt.title(f"Market net bps/turnover at {fee} bps fee")
        save(name)

    breakeven = frames["market_breakeven_by_date.csv"]
    for model, group in breakeven[breakeven["latency_ms"] == 0].groupby("model"):
        plt.plot(group["date"], group["breakeven_fee_bps"], marker="o", label=model)
    plt.xticks(rotation=45, ha="right")
    plt.axhline(0.5, color="red", linewidth=0.8)
    plt.legend()
    plt.title("Breakeven transaction cost by date and model")
    save("breakeven_fee_by_date_model.png")

    inc = frames["incremental_economics_by_date.csv"]
    ext = inc[
        (inc["comparison"] == "Extended_minus_QI")
        & (inc["latency_ms"] == 0)
        & (inc["fee_bps"] == 0.25)
    ]
    plt.plot(ext["date"], ext["delta_net_pnl"], marker="o")
    plt.xticks(rotation=45, ha="right")
    plt.axhline(0, color="black", linewidth=0.8)
    plt.title("Extended minus QI net increment by date")
    save("extended_minus_qi_increment_by_date.png")

    latency = frames["latency_robustness.csv"]
    for model, group in latency.groupby("model"):
        plt.plot(group["date"], group["gross_pnl_erosion"], marker="o", label=model)
    plt.xticks(rotation=45, ha="right")
    plt.axhline(0, color="black", linewidth=0.8)
    plt.legend()
    plt.title("0ms vs 100ms latency effect by date")
    save("latency_effect_by_date.png")

    size = frames["order_size_sensitivity.csv"]
    for model, group in size.groupby("model"):
        plotted = group.groupby("order_notional_usd")["gross_pnl_bps_of_turnover"].mean()
        plt.plot(plotted.index, plotted.values, marker="o", label=model)
    plt.xscale("log")
    plt.axhline(0, color="black", linewidth=0.8)
    plt.legend()
    plt.title("Displayed-book order-size sensitivity")
    save("displayed_book_order_size_sensitivity.png")

    passive = frames["passive_multiday_results.csv"]
    for model, group in passive[passive["latency_ms"] == 0].groupby("model"):
        plt.plot(group["date"], group["fill_rate"], marker="o", label=model)
    plt.xticks(rotation=45, ha="right")
    plt.legend()
    plt.title("Passive fill rate across dates")
    save("passive_fill_rate_across_dates.png")

    queue = frames["passive_queue_sensitivity.csv"]
    queue.groupby(["queue_fraction", "model"])["fill_rate"].mean().unstack("model").plot(kind="bar")
    plt.title("Passive queue-fraction sensitivity")
    save("passive_queue_fraction_sensitivity.png")

    ttl = frames["passive_ttl_sensitivity.csv"]
    ttl.groupby(["ttl_ms", "model"])["fill_rate"].mean().unstack("model").plot(kind="bar")
    plt.title("Passive TTL sensitivity")
    save("passive_ttl_sensitivity.png")

    for model, group in passive[passive["latency_ms"] == 0].groupby("model"):
        plt.plot(group["date"], group["terminal_position"], marker="o", label=model)
    plt.xticks(rotation=45, ha="right")
    plt.axhline(0, color="black", linewidth=0.8)
    plt.legend()
    plt.title("Passive terminal inventory across dates")
    save("passive_terminal_inventory_across_dates.png")

    markout_cols = [
        "average_signed_markout_100ms",
        "average_signed_markout_500ms",
        "average_signed_markout_1000ms",
        "average_signed_markout_5000ms",
    ]
    passive.groupby("date")[markout_cols].mean().plot(kind="bar")
    plt.title("Passive post-fill markouts across dates")
    save("passive_post_fill_markouts_across_dates.png")


def write_readme(output_dir: Path, summary: dict[str, Any]) -> None:
    text = f"""# Phase 14 Robustness Reports

Phase 14 tests whether Phase 11-13 execution and cost conclusions are stable
outside the single original execution date. Date selection is mechanical and
frozen before results.

- Phase 14 plan hash: `{summary["phase14_plan_hash"]}`
- Phase 14 robustness artifact hash: `{summary["phase14_robustness_artifact_hash"]}`
- Phase 14 results hash: recorded in `phase14_summary.json`
- Primary market dates: `{", ".join(PRIMARY_MARKET_DATES)}`
- Passive dates: `{", ".join(PASSIVE_DATES)}`

The outputs retain negative days, low passive fill rates, residual inventory,
and model underperformance cases. No 2026 holdout data, annualized metrics,
Sharpe ratio, model tuning, signal retuning, or strategy optimization is
reported.

`market_date_level_summary.csv` contains mean, median, minimum, maximum, and
positive/negative day counts by model, latency, and fee overlay.
"""
    (output_dir / "README.md").write_text(text, encoding="utf-8")


def run_phase14(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    artifact_root = Path(args.artifact_root)
    if args.clean:
        for path in (output_dir, artifact_root):
            if path.exists():
                shutil.rmtree(path)
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_root.mkdir(parents=True, exist_ok=True)
    config = load_yaml_config(args.execution_config)
    input_info = verify_inputs(args, config)
    derived_root = Path(config["real_data_diagnostic"]["derived_root"])

    market_cfg = scenario_config(config, dates=PRIMARY_MARKET_DATES, models=MODELS)
    market_root = artifact_root / "primary_market"
    market_entries = run_grid(
        config=market_cfg,
        dates=PRIMARY_MARKET_DATES,
        models=MODELS,
        mode="market",
        latencies_ms=MARKET_LATENCIES_MS,
        output_root=market_root,
    )
    market_base = pd.DataFrame(
        [
            base_metrics(entry=entry, artifact_root=market_root, derived_root=derived_root)
            for entry in market_entries
        ]
    )
    market_results = market_fee_rows(market_base)

    order_size_rows = []
    size_entries = []
    for notional in ORDER_SIZE_NOTIONAL_USD:
        size_cfg = scenario_config(
            config,
            dates=SENSITIVITY_DATES,
            models=MODELS,
            order_notional=notional,
        )
        size_root = artifact_root / f"order_size/notional={int(notional)}"
        entries = run_grid(
            config=size_cfg,
            dates=SENSITIVITY_DATES,
            models=MODELS,
            mode="market",
            latencies_ms=[0],
            output_root=size_root,
        )
        size_entries.extend(
            {"analysis": "order_size", "notional": notional, **entry} for entry in entries
        )
        order_size_rows.extend(
            base_metrics(
                entry=entry,
                artifact_root=size_root,
                derived_root=derived_root,
                scenario_extra={"order_notional_usd": notional},
            )
            for entry in entries
        )

    passive_cfg = scenario_config(config, dates=PASSIVE_DATES, models=MODELS)
    passive_root = artifact_root / "passive_primary"
    passive_entries = run_grid(
        config=passive_cfg,
        dates=PASSIVE_DATES,
        models=MODELS,
        mode="passive",
        latencies_ms=MARKET_LATENCIES_MS,
        output_root=passive_root,
    )
    passive_rows = [
        base_metrics(
            entry=entry,
            artifact_root=passive_root,
            derived_root=derived_root,
            scenario_extra={"queue_fraction": 1.0, "ttl_ms": 1000},
        )
        for entry in passive_entries
    ]

    queue_rows = []
    queue_entries = []
    for queue_fraction in QUEUE_FRACTIONS:
        queue_cfg = scenario_config(
            config,
            dates=QUEUE_TTL_DATES,
            models=MODELS,
            queue_fraction=queue_fraction,
        )
        queue_root = artifact_root / f"passive_queue/queue={queue_fraction}"
        entries = run_grid(
            config=queue_cfg,
            dates=QUEUE_TTL_DATES,
            models=MODELS,
            mode="passive",
            latencies_ms=[0],
            output_root=queue_root,
        )
        queue_entries.extend(
            {"analysis": "passive_queue", "queue_fraction": queue_fraction, **entry}
            for entry in entries
        )
        queue_rows.extend(
            base_metrics(
                entry=entry,
                artifact_root=queue_root,
                derived_root=derived_root,
                scenario_extra={"queue_fraction": queue_fraction, "ttl_ms": 1000},
            )
            for entry in entries
        )

    ttl_rows = []
    ttl_entries = []
    for ttl_ms in TTL_MS:
        ttl_cfg = scenario_config(config, dates=QUEUE_TTL_DATES, models=MODELS, ttl_ms=ttl_ms)
        ttl_root = artifact_root / f"passive_ttl/ttl={ttl_ms}"
        entries = run_grid(
            config=ttl_cfg,
            dates=QUEUE_TTL_DATES,
            models=MODELS,
            mode="passive",
            latencies_ms=[0],
            output_root=ttl_root,
        )
        ttl_entries.extend(
            {"analysis": "passive_ttl", "ttl_ms": ttl_ms, **entry} for entry in entries
        )
        ttl_rows.extend(
            base_metrics(
                entry=entry,
                artifact_root=ttl_root,
                derived_root=derived_root,
                scenario_extra={"queue_fraction": 1.0, "ttl_ms": ttl_ms},
            )
            for entry in entries
        )

    frames = {
        "market_multiday_results.csv": market_results,
        "market_date_level_summary.csv": deterministic_sort(
            date_level_summary(market_results, ["model", "latency_ms", "fee_bps"]),
            ["model", "latency_ms", "fee_bps"],
        ),
        "market_breakeven_by_date.csv": breakeven_report(market_base),
        "model_ranking_stability.csv": ranking_report(market_results),
        "incremental_economics_by_date.csv": incremental_report(market_results),
        "latency_robustness.csv": latency_report(market_base),
        "order_size_sensitivity.csv": deterministic_sort(
            pd.DataFrame(order_size_rows),
            ["date", "model", "order_notional_usd"],
        ),
        "passive_multiday_results.csv": deterministic_sort(
            pd.DataFrame(passive_rows),
            ["date", "model", "latency_ms"],
        ),
        "passive_queue_sensitivity.csv": deterministic_sort(
            pd.DataFrame(queue_rows),
            ["date", "model", "queue_fraction"],
        ),
        "passive_ttl_sensitivity.csv": deterministic_sort(
            pd.DataFrame(ttl_rows),
            ["date", "model", "ttl_ms"],
        ),
    }
    frames["passive_inventory_stress.csv"] = passive_inventory_stress(
        frames["passive_multiday_results.csv"]
    )
    ensure_negative_days_retained(frames["market_multiday_results.csv"])
    for filename, frame in frames.items():
        frame.to_csv(output_dir / filename, index=False)

    all_entries = [
        {"analysis": "primary_market", **entry} for entry in market_entries
    ] + [
        {"analysis": "passive_primary", **entry} for entry in passive_entries
    ] + size_entries + queue_entries + ttl_entries
    manifest_payload = {
        "artifact_identity": "phase14_robustness_artifacts_v1",
        "phase14_plan_hash": PHASE14_ROBUSTNESS_PLAN_HASH,
        "phase10_signal_artifact_hash": PHASE10_SIGNAL_ARTIFACT_HASH,
        "entries": sorted(
            [
                {key: value for key, value in entry.items() if key != "runtime_seconds"}
                for entry in all_entries
            ],
            key=lambda row: (
                row["analysis"],
                str(row.get("date")),
                str(row.get("model")),
                int(row.get("latency_ms", 0)),
                str(row.get("notional", "")),
                str(row.get("queue_fraction", "")),
                str(row.get("ttl_ms", "")),
            ),
        ),
    }
    artifact_hash = hash_config(manifest_payload)
    manifest = {"phase14_robustness_artifact_hash": artifact_hash, **manifest_payload}
    (output_dir / "robustness_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    summary_without_hash = {
        **input_info,
        "phase14_status": "PASS",
        "phase14_robustness_artifact_hash": artifact_hash,
        "primary_market_dates": PRIMARY_MARKET_DATES,
        "passive_dates": PASSIVE_DATES,
        "order_size_dates": SENSITIVITY_DATES,
        "passive_queue_ttl_dates": QUEUE_TTL_DATES,
        "market_scenario_rows": int(len(frames["market_multiday_results.csv"])),
        "passive_primary_rows": int(len(frames["passive_multiday_results.csv"])),
        "order_size_rows": int(len(frames["order_size_sensitivity.csv"])),
        "queue_sensitivity_rows": int(len(frames["passive_queue_sensitivity.csv"])),
        "ttl_sensitivity_rows": int(len(frames["passive_ttl_sensitivity.csv"])),
        "market_fee_bps": MARKET_FEE_BPS,
        "market_latencies_ms": MARKET_LATENCIES_MS,
    }
    write_readme(output_dir, summary_without_hash)
    write_figures(output_dir, frames)
    results_hash = phase14_results_hash(output_dir, summary_without_hash)
    summary = {**summary_without_hash, "phase14_results_hash": results_hash}
    (output_dir / "phase14_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    args = parse_args()
    summary = run_phase14(args)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
