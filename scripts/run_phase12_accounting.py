"""Run Phase 12 deterministic portfolio accounting from frozen Phase 11 fills."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from microalpha.accounting.ledger import (
    PHASE11_COMMIT_SHA,
    PHASE11_EXECUTION_ARTIFACT_HASH,
    PHASE11_RESULTS_HASH,
    PHASE12_ACCOUNTING_PLAN_HASH,
    Fill,
    ScenarioKey,
    accounting_hash,
    build_ledger,
    check_cash_conservation,
    check_equity_identity,
    check_fee_reconciliation,
    check_fill_conservation,
    check_parent_child_reconciliation,
)
from microalpha.config import load_yaml_config
from microalpha.utils.hashing import hash_config

PHASE12_REPORTS = [
    "accounting_summary.csv",
    "pnl_by_scenario.csv",
    "inventory_summary.csv",
    "turnover_summary.csv",
    "realized_unrealized_summary.csv",
    "execution_pnl_decomposition.csv",
    "accounting_manifest.json",
    "README.md",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", default="data/manifests/phase12_accounting_plan.yaml")
    parser.add_argument("--phase11-manifest", default="reports/phase11/execution_manifest.json")
    parser.add_argument("--phase11-root", default="/tmp/microalpha-phase11")
    parser.add_argument("--derived-root", default="/tmp/microalpha-multiday/derived")
    parser.add_argument("--signal-root", default="/tmp/microalpha-phase10/signals")
    parser.add_argument("--ledger-root", default="/tmp/microalpha-phase12")
    parser.add_argument("--output-dir", default="reports/phase12")
    parser.add_argument("--clean", action="store_true")
    return parser.parse_args()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_inputs(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    plan_hash = hash_config(load_yaml_config(args.plan))
    if plan_hash != PHASE12_ACCOUNTING_PLAN_HASH:
        raise ValueError(f"Phase 12 accounting plan hash mismatch: {plan_hash}")
    phase11_manifest = json.loads(Path(args.phase11_manifest).read_text(encoding="utf-8"))
    manifest_payload = {
        key: value
        for key, value in phase11_manifest.items()
        if key != "phase11_execution_artifact_hash"
    }
    if hash_config(manifest_payload) != PHASE11_EXECUTION_ARTIFACT_HASH:
        raise ValueError("Phase 11 execution manifest hash mismatch")
    root = Path(args.phase11_root)
    for entry in phase11_manifest["entries"]:
        base = root / entry["relative_dir"]
        for kind in ("orders", "fills", "markouts"):
            path = base / f"{kind}.parquet"
            expected = entry[f"{kind}_sha256"]
            if sha256_file(path) != expected:
                raise ValueError(f"Phase 11 {kind} checksum mismatch for {entry['relative_dir']}")
    input_info = {
        "phase11_commit_sha": PHASE11_COMMIT_SHA,
        "phase11_tests_run_id": 31395063031,
        "phase11_research_smoke_run_id": 31395063336,
        "phase11_execution_artifact_hash": PHASE11_EXECUTION_ARTIFACT_HASH,
        "phase11_results_hash": PHASE11_RESULTS_HASH,
        "phase12_accounting_plan_hash": PHASE12_ACCOUNTING_PLAN_HASH,
        "phase11_manifest_entries_verified": len(phase11_manifest["entries"]),
    }
    return input_info, phase11_manifest["entries"]


def read_marks(derived_root: Path, date: str) -> pd.DataFrame:
    if date.startswith("2026-"):
        raise ValueError("Phase 12 must not access 2026 holdout dates")
    path = derived_root / f"date={date}" / "research_100ms.parquet"
    marks = pd.read_parquet(path, columns=["observation_time", "mid"]).rename(
        columns={"observation_time": "timestamp", "mid": "mark_mid"}
    )
    marks["timestamp"] = pd.to_datetime(marks["timestamp"], utc=True)
    marks["mark_mid"] = pd.to_numeric(marks["mark_mid"], errors="coerce")
    return marks.dropna(subset=["mark_mid"]).reset_index(drop=True)


def read_scenario_frames(
    *,
    phase11_root: Path,
    entry: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    base = phase11_root / entry["relative_dir"]
    orders = pd.read_parquet(base / "orders.parquet")
    fills = pd.read_parquet(base / "fills.parquet")
    fills = fills.reset_index(drop=True)
    fills["child_index"] = fills.groupby("order_id").cumcount()
    fills["fill_id"] = [
        f"{entry['relative_dir']}:{row.order_id}:{int(row.child_index)}"
        for row in fills.itertuples(index=False)
    ]
    return orders, fills


def fill_records(fills: pd.DataFrame) -> list[Fill]:
    records: list[Fill] = []
    for row in fills.itertuples(index=False):
        records.append(
            Fill(
                fill_id=str(row.fill_id),
                order_id=str(row.order_id),
                fill_time=pd.Timestamp(row.fill_time).to_pydatetime(),
                side=str(row.side),
                price=float(row.price),
                quantity=float(row.quantity),
                signed_quantity=float(row.signed_quantity),
                fee_quote=float(row.fee_quote),
                child_index=int(row.child_index),
            )
        )
    return records


def position_summary(ledger: pd.DataFrame) -> dict[str, float]:
    position = ledger["position"].astype(float)
    abs_position = position.abs()
    exposure = (position * ledger["mark_mid"].astype(float)).abs()
    return {
        "terminal_position": float(position.iloc[-1]),
        "max_long_position": float(position.max()),
        "max_short_position": float(position.min()),
        "max_abs_position": float(abs_position.max()),
        "mean_abs_position": float(abs_position.mean()),
        "median_abs_position": float(abs_position.median()),
        "time_weighted_abs_inventory": float(abs_position.mean()),
        "long_timestamp_fraction": float((position > 0).mean()),
        "flat_timestamp_fraction": float((position.abs() <= 1e-10).mean()),
        "short_timestamp_fraction": float((position < 0).mean()),
        "max_exposure": float(exposure.max()),
        "mean_exposure": float(exposure.mean()),
        "median_exposure": float(exposure.median()),
        "p95_exposure": float(exposure.quantile(0.95)),
    }


def mismatch_summary(
    *,
    ledger: pd.DataFrame,
    signal_root: Path,
    scenario: ScenarioKey,
    median_order_quantity: float,
) -> dict[str, float]:
    signal_path = (
        signal_root
        / f"date={scenario.date}"
        / f"model={scenario.model}"
        / "signals.parquet"
    )
    signals = pd.read_parquet(signal_path, columns=["signal_timestamp", "final_signal"])
    signals = signals.rename(columns={"signal_timestamp": "timestamp", "final_signal": "desired"})
    signals["timestamp"] = pd.to_datetime(signals["timestamp"], utc=True)
    frame = pd.merge_asof(
        ledger[["timestamp", "position"]].assign(
            timestamp=pd.to_datetime(ledger["timestamp"], utc=True)
        ),
        signals.sort_values("timestamp"),
        on="timestamp",
        direction="backward",
    )
    desired = frame["desired"].fillna(0).astype(int)
    position = frame["position"].astype(float)
    active = desired != 0
    if not active.any():
        return {
            "desired_active_actual_zero_fraction": 0.0,
            "direction_mismatch_fraction": 0.0,
            "partially_attained_fraction": 0.0,
        }
    actual_zero = position.abs() <= 1e-10
    direction_mismatch = active & (np.sign(position) != np.sign(desired)) & ~actual_zero
    threshold = max(float(median_order_quantity), 1e-12)
    partial = active & ~actual_zero & ~direction_mismatch & (position.abs() < threshold)
    return {
        "desired_active_actual_zero_fraction": float((active & actual_zero).sum() / active.sum()),
        "direction_mismatch_fraction": float(direction_mismatch.sum() / active.sum()),
        "partially_attained_fraction": float(partial.sum() / active.sum()),
    }


def scenario_reports(
    *,
    scenario: ScenarioKey,
    orders: pd.DataFrame,
    fills: pd.DataFrame,
    ledger: pd.DataFrame,
    summary: dict[str, Any],
    signal_root: Path,
) -> dict[str, dict[str, Any]]:
    final = ledger.iloc[-1]
    turnover = float(summary["turnover"])
    parent_order_count = int(len(orders))
    fill_count = int(len(fills))
    fees = float(summary["fees_paid"])
    median_quantity = float(orders["quantity"].median()) if not orders.empty else 0.0
    mismatch = mismatch_summary(
        ledger=ledger,
        signal_root=signal_root,
        scenario=scenario,
        median_order_quantity=median_quantity,
    )
    inventory = position_summary(ledger)
    common = {
        "date": scenario.date,
        "model": scenario.model,
        "mode": scenario.mode,
        "latency_ms": scenario.latency_ms,
    }
    pnl = {
        **common,
        "parent_order_count": parent_order_count,
        "fill_count": fill_count,
        "turnover": turnover,
        "gross_pnl": float(final["gross_pnl"]),
        "recorded_fees": fees,
        "net_pnl": float(final["net_pnl"]),
        "gross_pnl_bps_of_turnover": (
            0.0 if turnover == 0 else float(final["gross_pnl"]) / turnover * 10000
        ),
        "net_pnl_bps_of_turnover": (
            0.0 if turnover == 0 else float(final["net_pnl"]) / turnover * 10000
        ),
        "pnl_per_parent_order": 0.0
        if parent_order_count == 0
        else float(final["net_pnl"]) / parent_order_count,
        "pnl_per_fill": 0.0 if fill_count == 0 else float(final["net_pnl"]) / fill_count,
        "realized_pnl": float(final["realized_pnl"]),
        "terminal_unrealized_pnl": float(final["unrealized_pnl"]),
        "terminal_position": float(final["position"]),
        "terminal_mark_mid": float(final["mark_mid"]),
        "terminal_inventory_value": float(final["inventory_market_value"]),
        "max_abs_position": inventory["max_abs_position"],
    }
    turnover_row = {
        **common,
        "total_turnover": turnover,
        "buy_notional": float(summary["buy_notional"]),
        "sell_notional": float(summary["sell_notional"]),
        "gross_traded_btc_quantity": float(summary["gross_traded_quantity"]),
        "fill_count": fill_count,
        "parent_order_count": parent_order_count,
    }
    realized = {
        **common,
        "gross_pnl": float(final["gross_pnl"]),
        "net_pnl": float(final["net_pnl"]),
        "realized_pnl": float(final["realized_pnl"]),
        "terminal_unrealized_pnl": float(final["unrealized_pnl"]),
        "recorded_fees": fees,
    }
    child_fill_count = (
        int((orders["filled_quantity"] > 0).sum()) if "filled_quantity" in orders else 0
    )
    passive_fields = {}
    if scenario.mode == "passive":
        passive_fields = {
            "filled_parent_orders": child_fill_count,
            "partial_fill_parent_orders": int((orders["status"] == "PARTIALLY_FILLED").sum()),
            **mismatch,
        }
    decomposition = {
        **common,
        "gross_trading_pnl": float(final["gross_pnl"]),
        "recorded_fees": fees,
        "net_pnl": float(final["net_pnl"]),
        "realized_pnl": float(final["realized_pnl"]),
        "terminal_unrealized_pnl": float(final["unrealized_pnl"]),
        "mean_implementation_shortfall": float(
            fills["implementation_shortfall_vs_decision_mid"].mean()
        )
        if "implementation_shortfall_vs_decision_mid" in fills
        else np.nan,
        "implementation_shortfall_not_identity_component": True,
    }
    return {
        "accounting": {**pnl, **passive_fields},
        "pnl": pnl,
        "inventory": {**common, **inventory, **mismatch},
        "turnover": turnover_row,
        "realized": realized,
        "decomposition": decomposition,
    }


def write_scenario_ledger(
    *,
    ledger_root: Path,
    scenario: ScenarioKey,
    ledger: pd.DataFrame,
) -> dict[str, Any]:
    relative_id = f"{scenario.relative_id}/ledger.parquet"
    output = ledger_root / relative_id
    output.parent.mkdir(parents=True, exist_ok=True)
    ledger.to_parquet(output, index=False)
    return {
        "relative_artifact_id": relative_id,
        "date": scenario.date,
        "model": scenario.model,
        "mode": scenario.mode,
        "latency_ms": scenario.latency_ms,
        "row_count": int(len(ledger)),
        "schema": {column: str(dtype) for column, dtype in ledger.dtypes.items()},
        "sha256": sha256_file(output),
        "ledger_hash": accounting_hash(ledger),
    }


def run_accounting(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    ledger_root = Path(args.ledger_root)
    if args.clean:
        if output_dir.exists():
            shutil.rmtree(output_dir)
        if ledger_root.exists():
            shutil.rmtree(ledger_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "figures").mkdir(parents=True, exist_ok=True)
    input_info, entries = verify_inputs(args)

    reports = {
        "accounting": [],
        "pnl": [],
        "inventory": [],
        "turnover": [],
        "realized": [],
        "decomposition": [],
    }
    manifest_entries = []
    ledgers_for_figures = []
    marks_by_date: dict[str, pd.DataFrame] = {}
    for entry in sorted(entries, key=lambda row: row["relative_dir"]):
        scenario = ScenarioKey(
            date=str(entry["date"]),
            model=str(entry["model"]),
            mode=str(entry["mode"]),
            latency_ms=int(entry["latency_ms"]),
        )
        if scenario.date.startswith("2026-"):
            raise ValueError("Phase 12 must not access 2026 holdout dates")
        if scenario.date not in marks_by_date:
            marks_by_date[scenario.date] = read_marks(Path(args.derived_root), scenario.date)
        orders, fills = read_scenario_frames(phase11_root=Path(args.phase11_root), entry=entry)
        check_parent_child_reconciliation(orders, fills)
        result = build_ledger(
            fills=fill_records(fills),
            marks=marks_by_date[scenario.date],
            scenario=scenario,
        )
        check_fill_conservation(result.ledger, result.fills)
        check_cash_conservation(result.ledger, result.fills)
        check_equity_identity(result.ledger)
        check_fee_reconciliation(result.ledger)
        manifest_entries.append(
            {
                **write_scenario_ledger(
                    ledger_root=ledger_root,
                    scenario=scenario,
                    ledger=result.ledger,
                ),
                "source_phase11_relative_dir": entry["relative_dir"],
                "source_orders_sha256": entry["orders_sha256"],
                "source_fills_sha256": entry["fills_sha256"],
                "source_markouts_sha256": entry["markouts_sha256"],
                "phase12_accounting_plan_hash": PHASE12_ACCOUNTING_PLAN_HASH,
                "phase11_execution_artifact_hash": PHASE11_EXECUTION_ARTIFACT_HASH,
            }
        )
        scenario_report = scenario_reports(
            scenario=scenario,
            orders=orders,
            fills=fills,
            ledger=result.ledger,
            summary=result.summary,
            signal_root=Path(args.signal_root),
        )
        for key, row in scenario_report.items():
            reports[key].append(row)
        sample = result.ledger.iloc[::1000].copy()
        ledgers_for_figures.append(sample)

    report_frames = {
        "accounting_summary.csv": pd.DataFrame(reports["accounting"]),
        "pnl_by_scenario.csv": pd.DataFrame(reports["pnl"]),
        "inventory_summary.csv": pd.DataFrame(reports["inventory"]),
        "turnover_summary.csv": pd.DataFrame(reports["turnover"]),
        "realized_unrealized_summary.csv": pd.DataFrame(reports["realized"]),
        "execution_pnl_decomposition.csv": pd.DataFrame(reports["decomposition"]),
    }
    for filename, frame in report_frames.items():
        frame.to_csv(output_dir / filename, index=False)

    manifest_payload = {
        "artifact_identity": "phase12_accounting_artifacts_v1",
        "phase12_accounting_plan_hash": PHASE12_ACCOUNTING_PLAN_HASH,
        "phase11_execution_artifact_hash": PHASE11_EXECUTION_ARTIFACT_HASH,
        "entries": sorted(manifest_entries, key=lambda row: row["relative_artifact_id"]),
    }
    accounting_artifact_hash = hash_config(manifest_payload)
    accounting_manifest = {
        "phase12_accounting_artifact_hash": accounting_artifact_hash,
        **manifest_payload,
    }
    (output_dir / "accounting_manifest.json").write_text(
        json.dumps(accounting_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    write_readme(output_dir)
    write_figures(
        figures_dir=output_dir / "figures",
        ledgers=pd.concat(ledgers_for_figures, ignore_index=True),
        reports=report_frames,
    )
    summary_without_hash = {
        **input_info,
        "phase12_status": "PASS",
        "phase12_accounting_artifact_hash": accounting_artifact_hash,
        "scenario_count": len(manifest_entries),
        "dates": sorted({entry["date"] for entry in manifest_entries}),
        "models": sorted({entry["model"] for entry in manifest_entries}),
        "modes": sorted({entry["mode"] for entry in manifest_entries}),
        "latency_scenarios_ms": sorted({int(entry["latency_ms"]) for entry in manifest_entries}),
        "total_parent_orders": int(
            report_frames["turnover_summary.csv"]["parent_order_count"].sum()
        ),
        "total_fills": int(report_frames["turnover_summary.csv"]["fill_count"].sum()),
        "total_turnover": float(report_frames["turnover_summary.csv"]["total_turnover"].sum()),
        "gross_pnl_by_scenario": report_frames["pnl_by_scenario.csv"][
            ["model", "mode", "latency_ms", "gross_pnl", "net_pnl"]
        ].to_dict(orient="records"),
    }
    results_hash = deterministic_results_hash(output_dir, summary_without_hash)
    summary = {**summary_without_hash, "phase12_results_hash": results_hash}
    (output_dir / "phase12_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def write_readme(output_dir: Path) -> None:
    text = f"""# Phase 12 Accounting Reports

Phase 12 consumes the frozen Phase 11 fills and builds isolated
self-financing ledgers. Each scenario starts with zero cash and zero inventory,
uses signed BTC quantities, marks inventory to the most recent observable
100ms mid price, and does not add a terminal liquidation fill.

- Phase 12 accounting plan hash: `{PHASE12_ACCOUNTING_PLAN_HASH}`
- Phase 11 execution artifact hash: `{PHASE11_EXECUTION_ARTIFACT_HASH}`
- Full ledger artifact root: `/tmp/microalpha-phase12`

The current real-data accounting scope is one development date,
`2024-07-01`. No annualized metrics, Sharpe ratio, or Phase 13 cost/latency
sweep is reported.
"""
    (output_dir / "README.md").write_text(text, encoding="utf-8")


def write_figures(
    *,
    figures_dir: Path,
    ledgers: pd.DataFrame,
    reports: dict[str, pd.DataFrame],
) -> None:
    ledgers = ledgers.copy()
    ledgers["timestamp"] = pd.to_datetime(ledgers["timestamp"], utc=True)

    def save(name: str) -> None:
        plt.tight_layout()
        plt.savefig(figures_dir / name, dpi=140)
        plt.close()

    market0 = ledgers[(ledgers["mode"] == "market") & (ledgers["latency_ms"] == 0)]
    for model, group in market0.groupby("model"):
        plt.plot(group["timestamp"], group["gross_pnl"], label=model)
    plt.legend()
    plt.title("Market 0ms gross PnL by model")
    save("market_0ms_gross_equity_by_model.png")

    market = ledgers[ledgers["mode"] == "market"]
    for latency, group in market.groupby("latency_ms"):
        plt.plot(group["timestamp"], group["net_pnl"], label=f"{latency}ms")
    plt.legend()
    plt.title("Market net PnL path by latency")
    save("market_latency_pnl_path.png")

    passive = ledgers[ledgers["mode"] == "passive"]
    for model, group in passive.groupby("model"):
        plt.plot(group["timestamp"], group["net_pnl"], label=model)
    plt.legend()
    plt.title("Passive net PnL path")
    save("passive_pnl_path.png")

    for model, group in market0.groupby("model"):
        plt.plot(group["timestamp"], group["position"], label=model)
    plt.legend()
    plt.title("Market 0ms position path")
    save("position_path.png")

    ledgers.groupby("timestamp")["inventory_market_value"].apply(lambda s: s.abs().mean()).plot()
    plt.title("Mean absolute inventory exposure")
    save("absolute_inventory_exposure.png")

    reports["pnl_by_scenario.csv"].plot(
        x="model",
        y=["gross_pnl", "net_pnl"],
        kind="bar",
    )
    plt.title("Gross vs net PnL")
    save("gross_vs_net_pnl.png")

    reports["realized_unrealized_summary.csv"].plot(
        x="model",
        y=["realized_pnl", "terminal_unrealized_pnl"],
        kind="bar",
    )
    plt.title("Realized vs terminal unrealized PnL")
    save("realized_vs_unrealized_terminal_pnl.png")

    reports["turnover_summary.csv"].plot(x="model", y="total_turnover", kind="bar")
    plt.title("Turnover by scenario")
    save("turnover_by_scenario.png")

    passive_inventory = reports["inventory_summary.csv"][
        reports["inventory_summary.csv"]["mode"] == "passive"
    ]
    passive_inventory.plot(
        x="model",
        y=[
            "desired_active_actual_zero_fraction",
            "direction_mismatch_fraction",
            "partially_attained_fraction",
        ],
        kind="bar",
    )
    plt.title("Passive desired vs actual mismatch")
    save("passive_desired_vs_actual_mismatch.png")

    reports["pnl_by_scenario.csv"].pivot_table(
        index="model",
        columns=["mode", "latency_ms"],
        values="net_pnl",
    ).plot(kind="bar")
    plt.title("Market vs passive accounting comparison")
    save("market_vs_passive_accounting_comparison.png")


def deterministic_results_hash(output_dir: Path, summary_without_hash: dict[str, Any]) -> str:
    payload = {
        "phase12_summary_without_results_hash": summary_without_hash,
    }
    for filename in PHASE12_REPORTS:
        payload[filename] = (output_dir / filename).read_text(encoding="utf-8")
    return hash_config(payload)


def main() -> None:
    args = parse_args()
    summary = run_accounting(args)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
