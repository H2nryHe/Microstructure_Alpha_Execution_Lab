"""Run Phase 13 transaction-cost, latency, and breakeven diagnostics."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from microalpha.accounting.ledger import Fill, ScenarioKey, build_ledger
from microalpha.config import load_yaml_config
from microalpha.research.phase13 import (
    PHASE12_ACCOUNTING_ARTIFACT_HASH,
    PHASE12_COMMIT_SHA,
    PHASE12_RESULTS_HASH,
    PHASE13_COST_LATENCY_PLAN_HASH,
    apply_fee_overlay,
    assert_net_pnl_monotonic_nonincreasing,
    interpolate_fee_breakeven,
    market_breakeven_fee_bps,
    passive_breakevens,
    role_turnover_and_fees,
    terminal_inventory_stress_rows,
    validate_latency_causality,
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
    verify_frozen_inputs,
)
from run_phase12_accounting import (
    PHASE12_ACCOUNTING_PLAN_HASH,
    read_marks,
)

LATENCY_GRID_MS = [0, 10, 50, 100, 250]
MARKET_FEE_BPS = [0.0, 0.10, 0.25, 0.50, 1.0, 2.0, 5.0, 10.0]
PASSIVE_FEE_SCENARIOS = [
    {"fee_scenario": "P0", "maker_fee_bps": 0.0, "taker_fee_bps": 0.0},
    {"fee_scenario": "P1", "maker_fee_bps": -0.50, "taker_fee_bps": 0.50},
    {"fee_scenario": "P2", "maker_fee_bps": 0.0, "taker_fee_bps": 0.50},
    {"fee_scenario": "P3", "maker_fee_bps": 0.0, "taker_fee_bps": 1.0},
    {"fee_scenario": "P4", "maker_fee_bps": 0.25, "taker_fee_bps": 1.0},
    {"fee_scenario": "P5", "maker_fee_bps": 0.50, "taker_fee_bps": 2.0},
    {"fee_scenario": "P6", "maker_fee_bps": 1.0, "taker_fee_bps": 5.0},
]
TERMINAL_MARK_SHOCK_BPS = [-10.0, -5.0, 0.0, 5.0, 10.0]
MODELS = ["qi_direct_baseline", "lightgbm_qi_ofi", "lightgbm_extended"]
REPORT_FILES = [
    "market_fee_sensitivity.csv",
    "passive_fee_sensitivity.csv",
    "latency_sensitivity.csv",
    "breakeven_costs.csv",
    "cost_survival.csv",
    "incremental_economics.csv",
    "passive_latency_diagnostic.csv",
    "terminal_inventory_stress.csv",
    "cost_decomposition.csv",
    "execution_grid_manifest.json",
    "README.md",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", default="data/manifests/phase13_cost_latency_plan.yaml")
    parser.add_argument("--execution-config", default="configs/execution.yaml")
    parser.add_argument("--phase11-plan", default="data/manifests/phase11_execution_plan.yaml")
    parser.add_argument("--phase10-manifest", default="reports/phase10/signal_manifest.json")
    parser.add_argument("--phase12-summary", default="reports/phase12/phase12_summary.json")
    parser.add_argument("--phase12-accounting", default="reports/phase12/accounting_summary.csv")
    parser.add_argument("--execution-root", default="/tmp/microalpha-phase13-execution")
    parser.add_argument("--ledger-root", default="/tmp/microalpha-phase13-ledgers")
    parser.add_argument("--output-dir", default="reports/phase13")
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--reuse-execution", action="store_true")
    return parser.parse_args()


def verify_phase13_inputs(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    plan = load_yaml_config(args.plan)
    plan_hash = hash_config(plan)
    if plan_hash != PHASE13_COST_LATENCY_PLAN_HASH:
        raise ValueError(f"Phase 13 plan hash mismatch: {plan_hash}")
    if str(plan["upstream_artifacts"]["phase12_commit_sha"]) != PHASE12_COMMIT_SHA:
        raise ValueError("Phase 12 commit mismatch in Phase 13 plan")
    plan_latencies = [int(value) for value in plan["latency_grid"]["order_arrival_latency_ms"]]
    if plan_latencies != LATENCY_GRID_MS:
        raise ValueError("Phase 13 latency grid mismatch")
    if [float(value) for value in plan["fee_grids"]["market_taker_fee_bps"]] != MARKET_FEE_BPS:
        raise ValueError("Phase 13 market fee grid mismatch")
    if str(config["real_data_diagnostic"]["dates"][0]).startswith("2026-"):
        raise ValueError("Phase 13 must not access 2026 holdout dates")
    if [str(value) for value in config["real_data_diagnostic"]["dates"]] != ["2024-07-01"]:
        raise ValueError("Phase 13 is frozen to the 2024-07-01 development date")
    if [str(value) for value in config["real_data_diagnostic"]["models"]] != MODELS:
        raise ValueError("Phase 13 models must remain frozen")

    phase11_info = verify_frozen_inputs(
        argparse.Namespace(
            config=args.execution_config,
            plan=args.phase11_plan,
            phase10_manifest=args.phase10_manifest,
        ),
        config,
    )
    phase12_summary = json.loads(Path(args.phase12_summary).read_text(encoding="utf-8"))
    if phase12_summary["phase12_accounting_artifact_hash"] != PHASE12_ACCOUNTING_ARTIFACT_HASH:
        raise ValueError("Phase 12 accounting artifact hash mismatch")
    if phase12_summary["phase12_results_hash"] != PHASE12_RESULTS_HASH:
        raise ValueError("Phase 12 results hash mismatch")
    return {
        **phase11_info,
        "phase12_commit_sha": PHASE12_COMMIT_SHA,
        "phase12_tests_run_id": 31397870924,
        "phase12_research_smoke_run_id": 31397869299,
        "phase12_accounting_plan_hash": PHASE12_ACCOUNTING_PLAN_HASH,
        "phase12_accounting_artifact_hash": PHASE12_ACCOUNTING_ARTIFACT_HASH,
        "phase12_results_hash": PHASE12_RESULTS_HASH,
        "phase13_plan_hash": PHASE13_COST_LATENCY_PLAN_HASH,
    }


def regenerate_execution_grid(
    *,
    config: dict[str, Any],
    execution_root: Path,
) -> tuple[
    list[dict[str, Any]],
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    dict[tuple[str, str], int],
]:
    derived_root = Path(config["real_data_diagnostic"]["derived_root"])
    source_root = Path(config["real_data_diagnostic"]["source_root"])
    dates = [str(date) for date in config["real_data_diagnostic"]["dates"]]
    models = [str(model) for model in config["real_data_diagnostic"]["models"]]
    entries: list[dict[str, Any]] = []
    all_orders = []
    all_fills = []
    all_markouts = []
    signals_by_key: dict[tuple[str, str], int] = {}
    depth = int(config["market_orders"]["max_book_depth_levels"])

    for date in dates:
        if date.startswith("2026-"):
            raise ValueError("Phase 13 must not access 2026 holdout dates")
        book_frame, book_times = read_book_frame(derived_root, date, depth)
        trades, trade_times = read_trades(source_root, date)
        for model in models:
            signal_file = (
                Path(config["real_data_diagnostic"]["signal_root"])
                / f"date={date}"
                / f"model={model}"
                / "signals.parquet"
            )
            signal_frame = pd.read_parquet(
                signal_file,
                columns=["signal_timestamp", "research_row_id", "final_signal"],
            )
            signals_by_key[(date, model)] = int((signal_frame["final_signal"] != 0).sum())
            signal_hash_before = sha256_file(signal_file)
            for mode in ("market", "passive"):
                for latency_ms in LATENCY_GRID_MS:
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
                        output_root=execution_root,
                    )
                    signal_hash_after = sha256_file(signal_file)
                    if signal_hash_after != signal_hash_before:
                        raise ValueError(f"Phase 10 signal artifact mutated: {signal_file}")
                    entries.append(entry)

    for entry in entries:
        base = execution_root / entry["relative_dir"]
        orders = pd.read_parquet(base / "orders.parquet")
        fills = pd.read_parquet(base / "fills.parquet")
        validate_latency_causality(orders, fills, latency_ms=int(entry["latency_ms"]))
        all_orders.append(orders)
        all_fills.append(fills)
        all_markouts.append(pd.read_parquet(base / "markouts.parquet"))
    return (
        entries,
        pd.concat(all_orders, ignore_index=True),
        pd.concat(all_fills, ignore_index=True),
        pd.concat(all_markouts, ignore_index=True),
        signals_by_key,
    )


def read_existing_execution_grid(
    *,
    execution_root: Path,
    manifest_path: Path,
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], str, dict[tuple[str, str], int]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["phase13_plan_hash"] != PHASE13_COST_LATENCY_PLAN_HASH:
        raise ValueError("Existing Phase 13 execution manifest plan hash mismatch")
    entries = list(manifest["entries"])
    signals_by_key: dict[tuple[str, str], int] = {}
    for date in [str(value) for value in config["real_data_diagnostic"]["dates"]]:
        for model in [str(value) for value in config["real_data_diagnostic"]["models"]]:
            signal_file = (
                Path(config["real_data_diagnostic"]["signal_root"])
                / f"date={date}"
                / f"model={model}"
                / "signals.parquet"
            )
            signal_frame = pd.read_parquet(signal_file, columns=["final_signal"])
            signals_by_key[(date, model)] = int((signal_frame["final_signal"] != 0).sum())
            for mode in ("market", "passive"):
                for latency_ms in LATENCY_GRID_MS:
                    relative_dir = f"{mode}/date={date}/model={model}/latency_ms={latency_ms}"
                    base = execution_root / relative_dir
                    orders_path = base / "orders.parquet"
                    fills_path = base / "fills.parquet"
                    markouts_path = base / "markouts.parquet"
                    paths_exist = (
                        orders_path.exists()
                        and fills_path.exists()
                        and markouts_path.exists()
                    )
                    if not paths_exist:
                        raise FileNotFoundError(
                            f"Missing existing Phase 13 execution files: {base}"
                        )
                    orders = pd.read_parquet(orders_path)
                    fills = pd.read_parquet(fills_path)
                    validate_latency_causality(orders, fills, latency_ms=latency_ms)
                    matching = [
                        entry for entry in entries if entry["relative_dir"] == relative_dir
                    ]
                    if len(matching) != 1:
                        raise ValueError(f"Missing manifest entry for {relative_dir}")
                    entry = matching[0]
                    checks = {
                        "orders_sha256": sha256_file(orders_path),
                        "fills_sha256": sha256_file(fills_path),
                        "markouts_sha256": sha256_file(markouts_path),
                    }
                    for key, actual in checks.items():
                        if entry[key] != actual:
                            raise ValueError(
                                f"Existing execution checksum mismatch: {relative_dir}"
                            )
    return entries, str(manifest["phase13_execution_grid_artifact_hash"]), signals_by_key


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


def scenario_base_reports(
    *,
    entries: list[dict[str, Any]],
    execution_root: Path,
    ledger_root: Path,
    derived_root: Path,
    output_dir: Path,
    signals_by_key: dict[tuple[str, str], int],
) -> tuple[pd.DataFrame, dict[tuple[str, str, str, int], pd.DataFrame]]:
    marks_by_date: dict[str, pd.DataFrame] = {}
    rows = []
    ledgers: dict[tuple[str, str, str, int], pd.DataFrame] = {}
    for entry in sorted(entries, key=lambda row: row["relative_dir"]):
        scenario = ScenarioKey(
            date=str(entry["date"]),
            model=str(entry["model"]),
            mode=str(entry["mode"]),
            latency_ms=int(entry["latency_ms"]),
        )
        if scenario.date not in marks_by_date:
            marks_by_date[scenario.date] = read_marks(derived_root, scenario.date)
        base = execution_root / entry["relative_dir"]
        orders = pd.read_parquet(base / "orders.parquet")
        fills = pd.read_parquet(base / "fills.parquet")
        markouts = pd.read_parquet(base / "markouts.parquet")
        result = build_ledger(
            fills=fill_records(fills),
            marks=marks_by_date[scenario.date],
            scenario=scenario,
        )
        ledger = result.ledger
        ledgers[(scenario.date, scenario.model, scenario.mode, scenario.latency_ms)] = ledger
        ledger_path = ledger_root / scenario.relative_id / "zero_fee_ledger.parquet"
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger.to_parquet(ledger_path, index=False)

        final = ledger.iloc[-1]
        turnover = float(result.summary["turnover"])
        order_count = int(len(orders))
        fill_count = int(len(fills))
        filled_parent_orders = int((orders["filled_quantity"].astype(float) > 0).sum())
        role_fees = role_turnover_and_fees(
            fills,
            maker_fee_bps=0.0,
            taker_fee_bps=0.0,
            force_all_taker=scenario.mode == "market",
        )
        active_transitions = max(order_count, 1)
        equity = ledger["gross_pnl"].astype(float)
        max_drawdown = float((equity.cummax() - equity).max())
        row = {
            "date": scenario.date,
            "model": scenario.model,
            "mode": scenario.mode,
            "latency_ms": scenario.latency_ms,
            "parent_orders": order_count,
            "fill_count": fill_count,
            "filled_parent_orders": filled_parent_orders,
            "fill_rate": 0.0 if order_count == 0 else filled_parent_orders / order_count,
            "mean_fill_fraction": float(orders["fill_fraction"].mean())
            if "fill_fraction" in orders
            else np.nan,
            "turnover": turnover,
            "turnover_per_parent_order": 0.0 if order_count == 0 else turnover / order_count,
            "turnover_per_active_signal_transition": turnover / active_transitions,
            "gross_pnl": float(final["gross_pnl"]),
            "gross_pnl_bps_of_turnover": 0.0
            if turnover == 0
            else float(final["gross_pnl"]) / turnover * 10_000.0,
            "net_pnl_zero_fee": float(final["net_pnl"]),
            "realized_pnl": float(final["realized_pnl"]),
            "terminal_unrealized_pnl": float(final["unrealized_pnl"]),
            "terminal_position": float(final["position"]),
            "terminal_mark_mid": float(final["mark_mid"]),
            "max_inventory": float(ledger["position"].astype(float).abs().max()),
            "intraday_max_drawdown": max_drawdown,
            "maker_turnover": role_fees.maker_turnover,
            "taker_turnover": role_fees.taker_turnover,
            "maker_fill_count": int((fills["liquidity_role"] == "maker").sum())
            if not fills.empty and "liquidity_role" in fills
            else 0,
            "taker_fill_count": int(
                fills["liquidity_role"].isin(["taker", "taker_marketable_limit"]).sum()
            )
            if not fills.empty and "liquidity_role" in fills
            else 0,
            "taker_on_arrival_count": int(
                (fills["liquidity_role"] == "taker_marketable_limit").sum()
            )
            if not fills.empty and "liquidity_role" in fills
            else 0,
            "mean_implementation_shortfall": float(
                fills["implementation_shortfall_vs_decision_mid"].mean()
            )
            if not fills.empty
            else np.nan,
            "average_signed_spread_vs_decision_mid": float(
                fills["signed_spread_vs_decision_mid"].mean()
            )
            if not fills.empty
            else np.nan,
            "average_signed_spread_vs_arrival_mid": float(
                fills["signed_spread_vs_arrival_mid"].mean()
            )
            if not fills.empty
            else np.nan,
            "average_signed_markout_100ms": float(markouts["signed_markout_100ms"].mean())
            if not markouts.empty
            else np.nan,
            "active_signal_transition_count": order_count,
            "active_signal_row_count": signals_by_key[(scenario.date, scenario.model)],
            "zero_fee_ledger_sha256": sha256_file(ledger_path),
        }
        rows.append(row)
    return pd.DataFrame(rows), ledgers


def verify_zero_fee_reconciliation(base: pd.DataFrame, phase12_path: Path) -> None:
    phase12 = pd.read_csv(phase12_path)
    compare_columns = [
        "parent_order_count",
        "fill_count",
        "turnover",
        "gross_pnl",
        "terminal_position",
    ]
    lookup = {
        (row.date, row.model, row.mode, int(row.latency_ms)): row
        for row in phase12.itertuples(index=False)
    }
    for row in base[base["latency_ms"].isin([0, 100])].itertuples(index=False):
        key = (row.date, row.model, row.mode, int(row.latency_ms))
        if key not in lookup:
            raise ValueError(f"Missing Phase 12 reconciliation row: {key}")
        expected = lookup[key]
        actual_values = {
            "parent_order_count": row.parent_orders,
            "fill_count": row.fill_count,
            "turnover": row.turnover,
            "gross_pnl": row.gross_pnl,
            "terminal_position": row.terminal_position,
        }
        for column in compare_columns:
            actual = float(actual_values[column])
            expected_value = float(getattr(expected, column))
            tolerance = max(1e-8, abs(expected_value) * 1e-11)
            if abs(actual - expected_value) > tolerance:
                raise ValueError(
                    f"Phase 12 zero-fee reconciliation failed for {key} {column}: "
                    f"{actual} != {expected_value}"
                )


def build_market_fee_sensitivity(base: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for scenario in base[base["mode"] == "market"].itertuples(index=False):
        fee_rows = []
        for fee_bps in MARKET_FEE_BPS:
            fees = role_turnover_and_fees(
                pd.DataFrame(
                    [
                        {
                            "price": 1.0,
                            "signed_quantity": scenario.turnover,
                            "liquidity_role": "taker",
                        }
                    ]
                ),
                maker_fee_bps=0.0,
                taker_fee_bps=fee_bps,
                force_all_taker=True,
            )
            overlay = apply_fee_overlay(scenario.gross_pnl, fees)
            row = {
                "date": scenario.date,
                "model": scenario.model,
                "mode": scenario.mode,
                "latency_ms": int(scenario.latency_ms),
                "fee_bps": fee_bps,
                "gross_pnl": overlay["gross_pnl"],
                "taker_fees": overlay["taker_fees"],
                "total_fees": overlay["total_fees"],
                "net_pnl": overlay["net_pnl"],
                "turnover": scenario.turnover,
                "net_pnl_bps_of_turnover": 0.0
                if scenario.turnover == 0
                else overlay["net_pnl"] / scenario.turnover * 10_000.0,
            }
            fee_rows.append(row)
            rows.append(row)
        assert_net_pnl_monotonic_nonincreasing(fee_rows)
    return pd.DataFrame(rows)


def build_passive_fee_sensitivity(base: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for scenario in base[base["mode"] == "passive"].itertuples(index=False):
        for fee_scenario in PASSIVE_FEE_SCENARIOS:
            fees = role_turnover_and_fees(
                pd.DataFrame(
                    [
                        {
                            "price": 1.0,
                            "signed_quantity": scenario.maker_turnover,
                            "liquidity_role": "maker",
                        },
                        {
                            "price": 1.0,
                            "signed_quantity": scenario.taker_turnover,
                            "liquidity_role": "taker_marketable_limit",
                        },
                    ]
                ),
                maker_fee_bps=float(fee_scenario["maker_fee_bps"]),
                taker_fee_bps=float(fee_scenario["taker_fee_bps"]),
            )
            overlay = apply_fee_overlay(scenario.gross_pnl, fees)
            rows.append(
                {
                    "date": scenario.date,
                    "model": scenario.model,
                    "mode": scenario.mode,
                    "latency_ms": int(scenario.latency_ms),
                    **fee_scenario,
                    "gross_pnl": overlay["gross_pnl"],
                    "maker_turnover": scenario.maker_turnover,
                    "taker_turnover": scenario.taker_turnover,
                    "maker_fees": overlay["maker_fees"],
                    "taker_fees": overlay["taker_fees"],
                    "total_fees": overlay["total_fees"],
                    "net_pnl": overlay["net_pnl"],
                    "turnover": scenario.turnover,
                    "net_pnl_bps_of_turnover": 0.0
                    if scenario.turnover == 0
                    else overlay["net_pnl"] / scenario.turnover * 10_000.0,
                    "fill_rate": scenario.fill_rate,
                    "maker_fill_count": scenario.maker_fill_count,
                    "taker_on_arrival_count": scenario.taker_on_arrival_count,
                    "mean_fill_fraction": scenario.mean_fill_fraction,
                    "terminal_position": scenario.terminal_position,
                    "max_inventory": scenario.max_inventory,
                }
            )
    return pd.DataFrame(rows)


def build_breakevens(
    base: pd.DataFrame,
    market_fee: pd.DataFrame,
) -> pd.DataFrame:
    rows = []
    for scenario in base.itertuples(index=False):
        common = {
            "date": scenario.date,
            "model": scenario.model,
            "mode": scenario.mode,
            "latency_ms": int(scenario.latency_ms),
            "gross_pnl": scenario.gross_pnl,
            "turnover": scenario.turnover,
        }
        if scenario.mode == "market":
            breakeven = market_breakeven_fee_bps(scenario.gross_pnl, scenario.turnover)
            subset = market_fee[
                (market_fee["date"] == scenario.date)
                & (market_fee["model"] == scenario.model)
                & (market_fee["latency_ms"] == int(scenario.latency_ms))
            ]
            rows.append(
                {
                    **common,
                    **breakeven,
                    "fee_grid_interpolated_breakeven_bps": interpolate_fee_breakeven(subset),
                    "maker_turnover": 0.0,
                    "taker_turnover": scenario.taker_turnover,
                }
            )
        else:
            for fee_scenario in PASSIVE_FEE_SCENARIOS:
                rows.append(
                    {
                        **common,
                        **fee_scenario,
                        **passive_breakevens(
                            gross_pnl=scenario.gross_pnl,
                            maker_turnover=scenario.maker_turnover,
                            taker_turnover=scenario.taker_turnover,
                            maker_fee_bps=float(fee_scenario["maker_fee_bps"]),
                            taker_fee_bps=float(fee_scenario["taker_fee_bps"]),
                        ),
                        "maker_turnover": scenario.maker_turnover,
                        "taker_turnover": scenario.taker_turnover,
                    }
                )
    return pd.DataFrame(rows)


def build_latency_sensitivity(base: pd.DataFrame) -> pd.DataFrame:
    rows = []
    market0 = {
        (row.model, row.mode): row.gross_pnl
        for row in base[(base["mode"] == "market") & (base["latency_ms"] == 0)].itertuples(
            index=False
        )
    }
    for scenario in base.itertuples(index=False):
        pnl_loss = np.nan
        pnl_loss_pct = np.nan
        if scenario.mode == "market":
            zero = market0[(scenario.model, scenario.mode)]
            pnl_loss = zero - scenario.gross_pnl
            pnl_loss_pct = np.nan if abs(zero) <= 1e-12 else pnl_loss / abs(zero)
        rows.append(
            {
                **scenario._asdict(),
                "pnl_loss_vs_0ms": pnl_loss,
                "pnl_loss_pct_vs_0ms": pnl_loss_pct,
            }
        )
    return pd.DataFrame(rows)


def build_survival(market_fee: pd.DataFrame, passive_fee: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for keys, group in market_fee.groupby(["date", "model", "mode", "latency_ms"], dropna=False):
        date, model, mode, latency_ms = keys
        row = {"date": date, "model": model, "execution_mode": mode, "latency_ms": latency_ms}
        for fee_bps in MARKET_FEE_BPS[1:]:
            value = group.loc[group["fee_bps"] == fee_bps, "net_pnl"].iloc[0] > 0
            row[f"net_positive_at_{format_bps(fee_bps)}bps"] = bool(value)
        rows.append(row)
    for keys, group in passive_fee.groupby(["date", "model", "mode", "latency_ms"], dropna=False):
        date, model, mode, latency_ms = keys
        row = {"date": date, "model": model, "execution_mode": mode, "latency_ms": latency_ms}
        for scenario in [item["fee_scenario"] for item in PASSIVE_FEE_SCENARIOS]:
            value = group.loc[group["fee_scenario"] == scenario, "net_pnl"].iloc[0] > 0
            row[f"net_positive_{scenario}"] = bool(value)
        rows.append(row)
    return pd.DataFrame(rows)


def build_incremental(market_fee: pd.DataFrame, base: pd.DataFrame) -> pd.DataFrame:
    rows = []
    comparisons = [
        ("lightgbm_extended", "qi_direct_baseline", "Extended_minus_QI"),
        ("lightgbm_qi_ofi", "qi_direct_baseline", "QI_OFI_minus_QI"),
        ("lightgbm_extended", "lightgbm_qi_ofi", "Extended_minus_QI_OFI"),
    ]
    market_base = base[base["mode"] == "market"]
    for latency_ms in LATENCY_GRID_MS:
        for left, right, label in comparisons:
            left_base = market_base[
                (market_base["model"] == left) & (market_base["latency_ms"] == latency_ms)
            ].iloc[0]
            right_base = market_base[
                (market_base["model"] == right) & (market_base["latency_ms"] == latency_ms)
            ].iloc[0]
            for fee_bps in MARKET_FEE_BPS:
                left_fee = market_fee[
                    (market_fee["model"] == left)
                    & (market_fee["latency_ms"] == latency_ms)
                    & (market_fee["fee_bps"] == fee_bps)
                ].iloc[0]
                right_fee = market_fee[
                    (market_fee["model"] == right)
                    & (market_fee["latency_ms"] == latency_ms)
                    & (market_fee["fee_bps"] == fee_bps)
                ].iloc[0]
                rows.append(
                    {
                        "comparison": label,
                        "left_model": left,
                        "right_model": right,
                        "latency_ms": latency_ms,
                        "fee_bps": fee_bps,
                        "gross_pnl_increment": left_base.gross_pnl - right_base.gross_pnl,
                        "turnover_increment": left_base.turnover - right_base.turnover,
                        "gross_pnl_bps_increment": left_base.gross_pnl_bps_of_turnover
                        - right_base.gross_pnl_bps_of_turnover,
                        "net_pnl_increment": left_fee.net_pnl - right_fee.net_pnl,
                    }
                )
    return pd.DataFrame(rows)


def build_passive_latency_diagnostic(base: pd.DataFrame) -> pd.DataFrame:
    rows = []
    passive = base[base["mode"] == "passive"]
    zero_rows = {
        row.model: row
        for row in passive[passive["latency_ms"] == 0].itertuples(index=False)
    }
    for scenario in passive.itertuples(index=False):
        zero = zero_rows[scenario.model]
        rows.append(
            {
                "date": scenario.date,
                "model": scenario.model,
                "latency_ms": int(scenario.latency_ms),
                "gross_pnl": scenario.gross_pnl,
                "gross_pnl_change_vs_0ms": scenario.gross_pnl - zero.gross_pnl,
                "fill_rate": scenario.fill_rate,
                "fill_rate_change_vs_0ms": scenario.fill_rate - zero.fill_rate,
                "maker_fill_count": scenario.maker_fill_count,
                "taker_on_arrival_count": scenario.taker_on_arrival_count,
                "maker_turnover": scenario.maker_turnover,
                "taker_turnover": scenario.taker_turnover,
                "mean_fill_fraction": scenario.mean_fill_fraction,
                "terminal_position": scenario.terminal_position,
                "terminal_position_change_vs_0ms": scenario.terminal_position
                - zero.terminal_position,
                "max_inventory": scenario.max_inventory,
                "average_signed_markout_100ms": scenario.average_signed_markout_100ms,
                "diagnostic_interpretation": (
                    "Descriptive only: latency changes fill selection, role mix, "
                    "fill rate, adverse-selection markouts, and residual inventory."
                ),
            }
        )
    return pd.DataFrame(rows)


def build_terminal_stress(base: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for scenario in base.itertuples(index=False):
        rows.extend(
            terminal_inventory_stress_rows(
                scenario={
                    "date": scenario.date,
                    "model": scenario.model,
                    "mode": scenario.mode,
                    "latency_ms": int(scenario.latency_ms),
                },
                terminal_position=scenario.terminal_position,
                terminal_mark_mid=scenario.terminal_mark_mid,
                base_terminal_equity=scenario.gross_pnl,
                shock_bps_values=TERMINAL_MARK_SHOCK_BPS,
            )
        )
    return pd.DataFrame(rows)


def build_cost_decomposition(market_fee: pd.DataFrame, passive_fee: pd.DataFrame) -> pd.DataFrame:
    market = market_fee.copy()
    market["fee_scenario"] = market["fee_bps"].map(lambda value: f"market_{format_bps(value)}bps")
    market["maker_fees"] = 0.0
    market["fee_burden_fraction_of_gross_pnl"] = market.apply(fee_burden, axis=1)
    passive = passive_fee.copy()
    passive["fee_burden_fraction_of_gross_pnl"] = passive.apply(fee_burden, axis=1)
    columns = [
        "date",
        "model",
        "mode",
        "latency_ms",
        "fee_scenario",
        "gross_pnl",
        "maker_fees",
        "taker_fees",
        "total_fees",
        "net_pnl",
        "fee_burden_fraction_of_gross_pnl",
    ]
    return pd.concat([market[columns], passive[columns]], ignore_index=True)


def fee_burden(row: pd.Series) -> float:
    gross = float(row["gross_pnl"])
    if gross <= 0:
        return np.nan
    return float(row["total_fees"]) / gross


def format_bps(value: float) -> str:
    return f"{value:.2f}".replace(".", "_")


def write_execution_manifest(
    *,
    output_dir: Path,
    entries: list[dict[str, Any]],
) -> str:
    manifest_entries = [
        {key: value for key, value in entry.items() if key != "runtime_seconds"}
        for entry in entries
    ]
    payload = {
        "artifact_identity": "phase13_execution_grid_artifacts_v1",
        "phase13_plan_hash": PHASE13_COST_LATENCY_PLAN_HASH,
        "phase11_execution_plan_hash": PHASE11_EXECUTION_PLAN_HASH,
        "phase11_execution_config_hash": PHASE11_EXECUTION_CONFIG_HASH,
        "phase10_signal_artifact_hash": PHASE10_SIGNAL_ARTIFACT_HASH,
        "latency_grid_ms": LATENCY_GRID_MS,
        "entries": sorted(manifest_entries, key=lambda row: row["relative_dir"]),
    }
    artifact_hash = hash_config(payload)
    manifest = {"phase13_execution_grid_artifact_hash": artifact_hash, **payload}
    (output_dir / "execution_grid_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return artifact_hash


def write_reports(
    *,
    output_dir: Path,
    frames: dict[str, pd.DataFrame],
    summary_without_hash: dict[str, Any],
) -> dict[str, Any]:
    figures_dir = output_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    for filename, frame in frames.items():
        frame.to_csv(output_dir / filename, index=False)
    write_readme(output_dir, summary_without_hash)
    write_figures(figures_dir=figures_dir, frames=frames)
    results_hash = deterministic_results_hash(output_dir, summary_without_hash)
    summary = {**summary_without_hash, "phase13_results_hash": results_hash}
    (output_dir / "phase13_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def write_readme(output_dir: Path, summary: dict[str, Any]) -> None:
    text = f"""# Phase 13 Cost and Latency Reports

Phase 13 regenerates execution causally for the frozen latency grid and then
applies generic transaction-fee overlays exactly once. Fill prices already
include bid/ask crossing, displayed-depth consumption, arrival-time market
state, and marketable-limit behavior, so spread and implementation shortfall
are diagnostics only and are not subtracted again.

- Phase 13 plan hash: `{summary["phase13_plan_hash"]}`
- Phase 13 execution grid artifact hash: `{summary["phase13_execution_grid_artifact_hash"]}`
- Phase 13 results hash: recorded in `phase13_summary.json`
- Date: `2024-07-01`
- Latency grid: `{LATENCY_GRID_MS}`
- Market fee grid bps: `{MARKET_FEE_BPS}`

The grids are generic research stress scenarios, not current exchange fee
schedules. The analysis remains a one-day development diagnostic with no
annualized metrics, Sharpe ratio, strategy optimization, or 2026 holdout access.
"""
    (output_dir / "README.md").write_text(text, encoding="utf-8")


def write_figures(*, figures_dir: Path, frames: dict[str, pd.DataFrame]) -> None:
    def save(name: str) -> None:
        plt.tight_layout()
        plt.savefig(figures_dir / name, dpi=140)
        plt.close()

    market_fee = frames["market_fee_sensitivity.csv"]
    for model, group in market_fee[market_fee["latency_ms"] == 0].groupby("model"):
        plt.plot(group["fee_bps"], group["net_pnl"], marker="o", label=model)
    plt.axhline(0, color="black", linewidth=0.8)
    plt.legend()
    plt.title("Market net PnL vs fee bps by model")
    save("market_net_pnl_vs_fee_bps_by_model.png")

    breakeven_costs = frames["breakeven_costs.csv"]
    market_be = breakeven_costs[breakeven_costs["breakeven_fee_status"] == "DEFINED"]
    market_be[market_be["mode"] == "market"].pivot(
        index="model",
        columns="latency_ms",
        values="breakeven_fee_bps",
    ).plot(kind="bar")
    plt.title("Market breakeven fee by model and latency")
    save("market_breakeven_fee_by_model_latency.png")

    latency = frames["latency_sensitivity.csv"]
    for (mode, model), group in latency.groupby(["mode", "model"]):
        plt.plot(group["latency_ms"], group["gross_pnl"], marker="o", label=f"{mode}:{model}")
    plt.axhline(0, color="black", linewidth=0.8)
    plt.legend(fontsize=7)
    plt.title("Gross PnL vs latency")
    save("gross_pnl_vs_latency.png")

    for (mode, model), group in latency.groupby(["mode", "model"]):
        plt.plot(
            group["latency_ms"],
            group["gross_pnl_bps_of_turnover"],
            marker="o",
            label=f"{mode}:{model}",
        )
    plt.axhline(0, color="black", linewidth=0.8)
    plt.legend(fontsize=7)
    plt.title("Gross PnL bps per turnover vs latency")
    save("gross_pnl_bps_vs_latency.png")

    incremental = frames["incremental_economics.csv"]
    ext_qi = incremental[
        (incremental["comparison"] == "Extended_minus_QI") & (incremental["latency_ms"] == 0)
    ]
    plt.plot(ext_qi["fee_bps"], ext_qi["net_pnl_increment"], marker="o")
    plt.axhline(0, color="black", linewidth=0.8)
    plt.title("Extended vs QI net PnL difference by fee")
    save("extended_vs_qi_net_pnl_difference_by_fee.png")

    latency.groupby(["mode", "model"])["turnover"].sum().unstack(0).plot(kind="bar")
    plt.title("Turnover by model")
    save("turnover_by_model.png")

    passive_fee = frames["passive_fee_sensitivity.csv"]
    passive_fee[passive_fee["latency_ms"] == 0].pivot(
        index="model",
        columns="fee_scenario",
        values="net_pnl",
    ).plot(kind="bar")
    plt.axhline(0, color="black", linewidth=0.8)
    plt.title("Passive net PnL by role-specific fee scenario")
    save("passive_net_pnl_by_role_fee_scenario.png")

    passive_latency = latency[latency["mode"] == "passive"]
    for model, group in passive_latency.groupby("model"):
        plt.plot(group["latency_ms"], group["fill_rate"], marker="o", label=model)
    plt.legend()
    plt.title("Passive fill rate vs latency")
    save("passive_fill_rate_vs_latency.png")

    for model, group in passive_latency.groupby("model"):
        plt.plot(group["latency_ms"], group["terminal_position"], marker="o", label=model)
    plt.axhline(0, color="black", linewidth=0.8)
    plt.legend()
    plt.title("Passive terminal inventory vs latency")
    save("passive_terminal_inventory_vs_latency.png")

    stress = frames["terminal_inventory_stress.csv"]
    passive_stress = stress[(stress["mode"] == "passive") & (stress["latency_ms"] == 0)]
    for model, group in passive_stress.groupby("model"):
        plt.plot(
            group["terminal_mark_shock_bps"],
            group["terminal_equity_delta"],
            marker="o",
            label=model,
        )
    plt.axhline(0, color="black", linewidth=0.8)
    plt.legend()
    plt.title("Terminal inventory mark-shock sensitivity")
    save("terminal_inventory_mark_shock_sensitivity.png")


def deterministic_results_hash(output_dir: Path, summary_without_hash: dict[str, Any]) -> str:
    payload: dict[str, Any] = {"phase13_summary_without_results_hash": summary_without_hash}
    for filename in REPORT_FILES:
        payload[filename] = (output_dir / filename).read_text(encoding="utf-8")
    return hash_config(payload)


def run_phase13(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    execution_root = Path(args.execution_root)
    ledger_root = Path(args.ledger_root)
    if args.clean and not args.reuse_execution:
        for path in (output_dir, execution_root, ledger_root):
            if path.exists():
                shutil.rmtree(path)
    elif args.clean and args.reuse_execution:
        if ledger_root.exists():
            shutil.rmtree(ledger_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    ledger_root.mkdir(parents=True, exist_ok=True)

    config = load_yaml_config(args.execution_config)
    input_info = verify_phase13_inputs(args, config)
    if args.reuse_execution:
        existing_manifest = Path(args.output_dir) / "execution_grid_manifest.json"
        if not existing_manifest.exists():
            raise FileNotFoundError("Cannot reuse execution without execution_grid_manifest.json")
        entries, execution_hash, signals_by_key = read_existing_execution_grid(
            execution_root=execution_root,
            manifest_path=existing_manifest,
            config=config,
        )
        manifest_target = output_dir / "execution_grid_manifest.json"
        if existing_manifest.resolve() != manifest_target.resolve():
            shutil.copyfile(existing_manifest, manifest_target)
    else:
        entries, _orders, _fills, _markouts, signals_by_key = regenerate_execution_grid(
            config=config,
            execution_root=execution_root,
        )
        execution_hash = write_execution_manifest(output_dir=output_dir, entries=entries)
    base, _ledgers = scenario_base_reports(
        entries=entries,
        execution_root=execution_root,
        ledger_root=ledger_root,
        derived_root=Path(config["real_data_diagnostic"]["derived_root"]),
        output_dir=output_dir,
        signals_by_key=signals_by_key,
    )
    verify_zero_fee_reconciliation(base, Path(args.phase12_accounting))

    market_fee = build_market_fee_sensitivity(base)
    passive_fee = build_passive_fee_sensitivity(base)
    latency = build_latency_sensitivity(base)
    breakevens = build_breakevens(base, market_fee)
    survival = build_survival(market_fee, passive_fee)
    incremental = build_incremental(market_fee, base)
    passive_diag = build_passive_latency_diagnostic(base)
    terminal_stress = build_terminal_stress(base)
    decomposition = build_cost_decomposition(market_fee, passive_fee)

    frames = {
        "market_fee_sensitivity.csv": market_fee,
        "passive_fee_sensitivity.csv": passive_fee,
        "latency_sensitivity.csv": latency,
        "breakeven_costs.csv": breakevens,
        "cost_survival.csv": survival,
        "incremental_economics.csv": incremental,
        "passive_latency_diagnostic.csv": passive_diag,
        "terminal_inventory_stress.csv": terminal_stress,
        "cost_decomposition.csv": decomposition,
    }
    market_be = breakevens[breakevens["mode"] == "market"][
        ["model", "latency_ms", "breakeven_fee_status", "breakeven_fee_bps"]
    ].to_dict(orient="records")
    summary_without_hash = {
        **input_info,
        "phase13_status": "PASS",
        "phase13_execution_grid_artifact_hash": execution_hash,
        "dates": ["2024-07-01"],
        "models": MODELS,
        "modes": ["market", "passive"],
        "latency_grid_ms": LATENCY_GRID_MS,
        "market_fee_grid_bps": MARKET_FEE_BPS,
        "passive_fee_scenarios": PASSIVE_FEE_SCENARIOS,
        "scenario_count": int(len(base)),
        "fee_overlay_scenario_count": int(len(market_fee) + len(passive_fee)),
        "market_breakeven_costs": market_be,
        "zero_fee_reconciliation": "PASS",
    }
    return write_reports(
        output_dir=output_dir,
        frames=frames,
        summary_without_hash=summary_without_hash,
    )


def main() -> None:
    args = parse_args()
    summary = run_phase13(args)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
