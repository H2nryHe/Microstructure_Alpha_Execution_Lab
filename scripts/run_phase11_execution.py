"""Run Phase 11 event-driven execution diagnostics."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from microalpha.config import load_yaml_config
from microalpha.execution.simulator import (
    PHASE10_COMMIT_SHA,
    PHASE10_RESULTS_HASH,
    PHASE10_SIGNAL_ARTIFACT_HASH,
    PHASE11_EXECUTION_CONFIG_HASH,
    PHASE11_EXECUTION_PLAN_HASH,
    BookSnapshot,
    OrderRequest,
    TradePrint,
    artifact_hash,
    execute_market_order,
    iso,
    make_order_id,
    simulate_limit_order,
    utc_datetime,
)
from microalpha.research.phase10 import signal_manifest_hash
from microalpha.utils.hashing import hash_config

HORIZONS_MS = (100, 500, 1000, 5000)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/execution.yaml")
    parser.add_argument("--plan", default="data/manifests/phase11_execution_plan.yaml")
    parser.add_argument("--phase10-manifest", default="reports/phase10/signal_manifest.json")
    parser.add_argument("--output-dir", default="reports/phase11")
    parser.add_argument("--clean", action="store_true")
    return parser.parse_args()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_frozen_inputs(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    config_hash = hash_config(config)
    if config_hash != PHASE11_EXECUTION_CONFIG_HASH:
        raise ValueError(f"Execution config hash mismatch: {config_hash}")
    plan_hash = hash_config(load_yaml_config(args.plan))
    if plan_hash != PHASE11_EXECUTION_PLAN_HASH:
        raise ValueError(f"Phase 11 plan hash mismatch: {plan_hash}")

    manifest = json.loads(Path(args.phase10_manifest).read_text(encoding="utf-8"))
    manifest_payload = {
        key: value for key, value in manifest.items() if key != "phase10_signal_artifact_hash"
    }
    if signal_manifest_hash(manifest_payload) != PHASE10_SIGNAL_ARTIFACT_HASH:
        raise ValueError("Phase 10 signal manifest hash mismatch")
    signal_root = Path(config["real_data_diagnostic"]["signal_root"])
    for entry in manifest["entries"]:
        artifact = signal_root / entry["relative_artifact_id"]
        if not artifact.exists():
            raise FileNotFoundError(f"Missing Phase 10 signal artifact: {artifact}")
        if sha256_file(artifact) != entry["sha256"]:
            raise ValueError(f"Phase 10 signal checksum mismatch: {entry['relative_artifact_id']}")
    return {
        "phase10_signal_entries_verified": len(manifest["entries"]),
        "phase10_signal_artifact_hash": PHASE10_SIGNAL_ARTIFACT_HASH,
        "phase10_results_hash": PHASE10_RESULTS_HASH,
        "phase10_commit_sha": PHASE10_COMMIT_SHA,
        "phase11_execution_plan_hash": PHASE11_EXECUTION_PLAN_HASH,
        "phase11_execution_config_hash": config_hash,
    }


def read_book_frame(derived_root: Path, date: str, depth: int) -> tuple[pd.DataFrame, np.ndarray]:
    path = derived_root / f"date={date}" / "research_100ms.parquet"
    if date.startswith("2026-"):
        raise ValueError("Phase 11 must not access 2026 holdout dates")
    columns = ["observation_time", "mid", "best_bid", "best_ask"]
    for level in range(1, depth + 1):
        columns.extend([f"bid_px_{level}", f"ask_px_{level}"])
        columns.append("bid_sz_1.1" if level == 1 else f"bid_sz_{level}")
        columns.append("ask_sz_1.1" if level == 1 else f"ask_sz_{level}")
    frame = pd.read_parquet(path, columns=columns).sort_values("observation_time")
    numeric_columns = [column for column in frame.columns if column != "observation_time"]
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["mid", "best_bid", "best_ask"]).reset_index(drop=True)
    frame["observation_time"] = pd.to_datetime(frame["observation_time"], utc=True)
    times = frame["observation_time"].astype("int64").to_numpy()
    return frame, times


def snapshot_from_row(row: pd.Series, depth: int) -> BookSnapshot:
    bids = []
    asks = []
    for level in range(1, depth + 1):
        bid_size_col = "bid_sz_1.1" if level == 1 else f"bid_sz_{level}"
        ask_size_col = "ask_sz_1.1" if level == 1 else f"ask_sz_{level}"
        bid_price = pd.to_numeric(row.get(f"bid_px_{level}"), errors="coerce")
        bid_size = pd.to_numeric(row.get(bid_size_col), errors="coerce")
        ask_price = pd.to_numeric(row.get(f"ask_px_{level}"), errors="coerce")
        ask_size = pd.to_numeric(row.get(ask_size_col), errors="coerce")
        if np.isfinite(bid_price) and np.isfinite(bid_size) and bid_size > 0:
            bids.append((float(bid_price), float(bid_size)))
        if np.isfinite(ask_price) and np.isfinite(ask_size) and ask_size > 0:
            asks.append((float(ask_price), float(ask_size)))
    if not bids or not asks:
        raise ValueError("Book snapshot requires bid and ask depth")
    return BookSnapshot(
        observation_time=utc_datetime(row["observation_time"].to_pydatetime()),
        bids=tuple(bids),
        asks=tuple(asks),
    )


def asof_index(times: np.ndarray, timestamp: pd.Timestamp) -> int:
    index = int(np.searchsorted(times, int(timestamp.value), side="right") - 1)
    if index < 0:
        raise ValueError(f"No book state at or before {timestamp}")
    return index


def future_mid(book_frame: pd.DataFrame, times: np.ndarray, timestamp: pd.Timestamp) -> float:
    index = int(np.searchsorted(times, int(timestamp.value), side="left"))
    if index >= len(book_frame):
        index = len(book_frame) - 1
    return float(book_frame.iloc[index]["mid"])


def read_trades(source_root: Path, date: str) -> tuple[pd.DataFrame, np.ndarray]:
    path = source_root / date / "BTCUSDT_trades.csv.gz"
    with gzip.open(path, "rt", encoding="utf-8", newline="") as file:
        frame = pd.read_csv(
            file,
            usecols=["local_timestamp", "side", "price", "amount"],
            dtype={"side": "string", "price": "float64", "amount": "float64"},
        )
    frame["observation_time"] = pd.to_datetime(frame["local_timestamp"], unit="us", utc=True)
    frame = frame.sort_values("observation_time").reset_index(drop=True)
    return frame, frame["observation_time"].astype("int64").to_numpy()


def trade_slice(
    trades: pd.DataFrame,
    trade_times: np.ndarray,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> list[TradePrint]:
    start_index = int(np.searchsorted(trade_times, int(start.value), side="right"))
    end_index = int(np.searchsorted(trade_times, int(end.value), side="right"))
    rows = trades.iloc[start_index:end_index]
    return [
        TradePrint(
            observation_time=utc_datetime(row.observation_time.to_pydatetime()),
            side=str(row.side),
            price=float(row.price),
            quantity=float(row.amount),
        )
        for row in rows.itertuples(index=False)
    ]


def transition_rows(signals: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    previous = 0
    for row in signals.itertuples(index=False):
        current = int(row.final_signal)
        if current == previous:
            continue
        rows.append(
            {
                "signal_timestamp": row.signal_timestamp,
                "research_row_id": int(row.research_row_id),
                "previous_signal": previous,
                "new_signal": current,
                "delta_units": current - previous,
            }
        )
        previous = current
    for index, row in enumerate(rows):
        row["next_transition_time"] = (
            rows[index + 1]["signal_timestamp"] if index + 1 < len(rows) else None
        )
    return rows


def order_from_transition(
    *,
    date: str,
    model: str,
    transition: dict[str, Any],
    mode: str,
    latency_ms: int,
    config: dict[str, Any],
    book_frame: pd.DataFrame,
    book_times: np.ndarray,
    depth: int,
) -> tuple[OrderRequest, BookSnapshot, BookSnapshot]:
    signal_time = pd.Timestamp(transition["signal_timestamp"])
    create_time = signal_time + pd.Timedelta(
        milliseconds=int(config["timing"]["decision_latency_ms"])
    )
    arrival_time = create_time + pd.Timedelta(milliseconds=latency_ms)
    create_snapshot = snapshot_from_row(
        book_frame.iloc[asof_index(book_times, create_time)],
        depth,
    )
    arrival_snapshot = snapshot_from_row(
        book_frame.iloc[asof_index(book_times, arrival_time)],
        depth,
    )
    delta_units = int(transition["delta_units"])
    side = "BUY" if delta_units > 0 else "SELL"
    quantity = (
        abs(delta_units)
        * float(config["order_sizing"]["target_order_notional_usd"])
        / create_snapshot.mid
    )
    limit_price = None
    expiration_time = None
    cancel_requested_time = None
    cancel_effective_time = None
    if mode == "passive":
        limit_price = create_snapshot.best_bid if side == "BUY" else create_snapshot.best_ask
        expiration_time = arrival_time + pd.Timedelta(
            milliseconds=int(config["passive_orders"]["limit_ttl_ms"])
        )
        next_time = transition.get("next_transition_time")
        if next_time is not None:
            next_signal_time = pd.Timestamp(next_time)
            if next_signal_time < expiration_time:
                cancel_requested_time = next_signal_time
                cancel_effective_time = next_signal_time + pd.Timedelta(
                    milliseconds=int(config["timing"]["cancel_latency_ms"])
                )
    order_type = "MARKET" if mode == "market" else "LIMIT"
    order_id = make_order_id(date, model, mode, latency_ms, transition["research_row_id"], side)
    order = OrderRequest(
        order_id=order_id,
        date=date,
        signal_id=f"{date}:{model}:{transition['research_row_id']}",
        model=model,
        side=side,
        order_type=order_type,
        quantity=quantity,
        limit_price=limit_price,
        order_create_time=utc_datetime(create_time.to_pydatetime()),
        order_arrival_time=utc_datetime(arrival_time.to_pydatetime()),
        remaining_quantity=quantity,
        cancel_requested_time=utc_datetime(cancel_requested_time.to_pydatetime())
        if cancel_requested_time is not None
        else None,
        cancel_effective_time=utc_datetime(cancel_effective_time.to_pydatetime())
        if cancel_effective_time is not None
        else None,
        expiration_time=utc_datetime(expiration_time.to_pydatetime())
        if expiration_time is not None
        else None,
    )
    return order, create_snapshot, arrival_snapshot


def fill_records_with_markouts(
    fills: list[Any],
    book_frame: pd.DataFrame,
    book_times: np.ndarray,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    fill_rows = []
    markout_rows = []
    for fill in fills:
        row = asdict(fill)
        row["fill_time"] = iso(fill.fill_time)
        row["order_arrival_time"] = iso(fill.order_arrival_time)
        fill_rows.append(row)
        markout = {
            "order_id": fill.order_id,
            "date": fill.date,
            "model": fill.model,
            "side": fill.side,
            "order_type": fill.order_type,
            "fill_time": iso(fill.fill_time),
            "fill_price": fill.price,
        }
        fill_time = pd.Timestamp(fill.fill_time)
        side_sign = 1 if fill.side == "BUY" else -1
        for horizon in HORIZONS_MS:
            mid = future_mid(book_frame, book_times, fill_time + pd.Timedelta(milliseconds=horizon))
            markout[f"signed_markout_{horizon}ms"] = side_sign * (mid - fill.price) / fill.price
        markout_rows.append(markout)
    return fill_rows, markout_rows


def run_scenario(
    *,
    date: str,
    model: str,
    mode: str,
    latency_ms: int,
    config: dict[str, Any],
    book_frame: pd.DataFrame,
    book_times: np.ndarray,
    trades: pd.DataFrame,
    trade_times: np.ndarray,
    output_root: Path,
) -> dict[str, Any]:
    depth = int(config["market_orders"]["max_book_depth_levels"])
    signal_path = (
        Path(config["real_data_diagnostic"]["signal_root"])
        / f"date={date}"
        / f"model={model}"
        / "signals.parquet"
    )
    signals = pd.read_parquet(
        signal_path,
        columns=["signal_timestamp", "research_row_id", "final_signal"],
    ).sort_values("signal_timestamp")
    signals["signal_timestamp"] = pd.to_datetime(signals["signal_timestamp"], utc=True)
    transitions = transition_rows(signals)
    order_rows: list[dict[str, Any]] = []
    fill_rows: list[dict[str, Any]] = []
    markout_rows: list[dict[str, Any]] = []
    start_time = time.perf_counter()

    for transition in transitions:
        order, create_snapshot, arrival_snapshot = order_from_transition(
            date=date,
            model=model,
            transition=transition,
            mode=mode,
            latency_ms=latency_ms,
            config=config,
            book_frame=book_frame,
            book_times=book_times,
            depth=depth,
        )
        if mode == "market":
            result, fills = execute_market_order(
                order,
                arrival_snapshot,
                fee_bps=float(config["fees"]["primary_real_data_fee_bps"]),
                decision_mid=create_snapshot.mid,
            )
        else:
            end_time = pd.Timestamp(order.expiration_time)
            if (
                order.cancel_effective_time is not None
                and pd.Timestamp(order.cancel_effective_time) < end_time
            ):
                end_time = pd.Timestamp(order.cancel_effective_time)
            eligible_trades = trade_slice(
                trades,
                trade_times,
                pd.Timestamp(order.order_arrival_time),
                end_time,
            )
            result, fills = simulate_limit_order(
                order,
                arrival_snapshot,
                eligible_trades,
                fee_bps=float(config["fees"]["primary_real_data_fee_bps"]),
                queue_fraction=float(config["passive_orders"]["queue_fraction"]),
                decision_mid=create_snapshot.mid,
            )
        result["mode"] = mode
        result["latency_ms"] = latency_ms
        result["decision_mid"] = create_snapshot.mid
        result["arrival_mid"] = arrival_snapshot.mid
        result["signed_decision_to_arrival_mid_move"] = (
            (1 if order.side == "BUY" else -1)
            * (arrival_snapshot.mid - create_snapshot.mid)
            / create_snapshot.mid
        )
        order_rows.append(result)
        scenario_fill_rows, scenario_markouts = fill_records_with_markouts(
            fills,
            book_frame,
            book_times,
        )
        for fill_row in scenario_fill_rows:
            fill_row["mode"] = mode
            fill_row["latency_ms"] = latency_ms
        for markout_row in scenario_markouts:
            markout_row["mode"] = mode
            markout_row["latency_ms"] = latency_ms
        fill_rows.extend(scenario_fill_rows)
        markout_rows.extend(scenario_markouts)

    relative_dir = Path(f"{mode}/date={date}/model={model}/latency_ms={latency_ms}")
    artifact_dir = output_root / relative_dir
    artifact_dir.mkdir(parents=True, exist_ok=True)
    orders_path = artifact_dir / "orders.parquet"
    fills_path = artifact_dir / "fills.parquet"
    markouts_path = artifact_dir / "markouts.parquet"
    pd.DataFrame(order_rows).to_parquet(orders_path, index=False)
    pd.DataFrame(fill_rows).to_parquet(fills_path, index=False)
    pd.DataFrame(markout_rows).to_parquet(markouts_path, index=False)
    return {
        "date": date,
        "model": model,
        "mode": mode,
        "latency_ms": latency_ms,
        "orders": len(order_rows),
        "fills": len(fill_rows),
        "runtime_seconds": time.perf_counter() - start_time,
        "relative_dir": str(relative_dir),
        "orders_sha256": sha256_file(orders_path),
        "fills_sha256": sha256_file(fills_path),
        "markouts_sha256": sha256_file(markouts_path),
        "order_state_hash": artifact_hash(order_rows),
        "fill_state_hash": artifact_hash(fill_rows),
    }


def summarize_orders(
    orders: pd.DataFrame,
    signals_by_key: dict[tuple[str, str], int],
) -> pd.DataFrame:
    rows = []
    for keys, group in orders.groupby(["date", "model", "mode", "latency_ms"], dropna=False):
        date, model, mode, latency = keys
        active_rows = signals_by_key[(date, model)]
        active_hours = active_rows / 3600.0
        rows.append(
            {
                "date": date,
                "model": model,
                "mode": mode,
                "latency_ms": latency,
                "order_count": len(group),
                "orders_per_day": len(group),
                "orders_per_active_signal_hour": len(group) / active_hours if active_hours else 0.0,
                "buy_orders": int((group["side"] == "BUY").sum()),
                "sell_orders": int((group["side"] == "SELL").sum()),
                "orders_per_signal_transition": 1.0,
            }
        )
    return pd.DataFrame(rows)


def summarize_market(
    orders: pd.DataFrame,
    fills: pd.DataFrame,
    markouts: pd.DataFrame,
) -> pd.DataFrame:
    market_orders = orders[orders["mode"] == "market"]
    market_fills = fills[fills["mode"] == "market"]
    rows = []
    for keys, group in market_orders.groupby(["date", "model", "latency_ms"], dropna=False):
        date, model, latency = keys
        child = market_fills[
            (market_fills["date"] == date)
            & (market_fills["model"] == model)
            & (market_fills["latency_ms"] == latency)
        ]
        marks = markouts[
            (markouts["date"] == date)
            & (markouts["model"] == model)
            & (markouts["latency_ms"] == latency)
            & (markouts["mode"] == "market")
        ]
        rows.append(
            {
                "date": date,
                "model": model,
                "latency_ms": latency,
                "order_count": len(group),
                "fill_count": int((group["filled_quantity"] > 0).sum()),
                "fill_rate": float((group["filled_quantity"] > 0).mean()),
                "partial_fill_count": int((group["status"] == "PARTIALLY_FILLED").sum()),
                "average_levels_consumed": float(group["levels_consumed"].mean()),
                "average_implementation_shortfall": float(
                    child["implementation_shortfall_vs_decision_mid"].mean()
                ),
                "median_implementation_shortfall": float(
                    child["implementation_shortfall_vs_decision_mid"].median()
                ),
                "p95_implementation_shortfall": float(
                    child["implementation_shortfall_vs_decision_mid"].quantile(0.95)
                ),
                "average_signed_decision_to_arrival_mid_move": float(
                    group["signed_decision_to_arrival_mid_move"].mean()
                ),
                **_mean_markouts(marks),
            }
        )
    return pd.DataFrame(rows)


def summarize_passive(
    orders: pd.DataFrame,
    fills: pd.DataFrame,
    markouts: pd.DataFrame,
) -> pd.DataFrame:
    passive_orders = orders[orders["mode"] == "passive"]
    passive_fills = fills[fills["mode"] == "passive"]
    rows = []
    for keys, group in passive_orders.groupby(["date", "model", "latency_ms"], dropna=False):
        date, model, latency = keys
        child = passive_fills[
            (passive_fills["date"] == date)
            & (passive_fills["model"] == model)
            & (passive_fills["latency_ms"] == latency)
        ]
        marks = markouts[
            (markouts["date"] == date)
            & (markouts["model"] == model)
            & (markouts["latency_ms"] == latency)
            & (markouts["mode"] == "passive")
        ]
        no_fill = group["filled_quantity"] <= 0
        rows.append(
            {
                "date": date,
                "model": model,
                "latency_ms": latency,
                "order_count": len(group),
                "full_fill_count": int((group["status"] == "FILLED").sum()),
                "partial_fill_count": int((group["status"] == "PARTIALLY_FILLED").sum()),
                "no_fill_or_expired_count": int(no_fill.sum()),
                "fill_rate": float((group["filled_quantity"] > 0).mean()),
                "mean_fill_fraction": float(group["fill_fraction"].mean()),
                "median_time_to_first_fill_ms": float(group["time_to_first_fill_ms"].median()),
                "median_time_to_full_fill_ms": float(group["time_to_full_fill_ms"].median()),
                "maker_fill_count": int((child["liquidity_role"] == "maker").sum())
                if not child.empty
                else 0,
                "taker_on_arrival_count": int(
                    (child["liquidity_role"] == "taker_marketable_limit").sum()
                )
                if not child.empty
                else 0,
                "average_queue_ahead_at_arrival": float(group["queue_ahead_at_arrival"].mean()),
                **_mean_markouts(marks),
            }
        )
    return pd.DataFrame(rows)


def _mean_markouts(markouts: pd.DataFrame) -> dict[str, float]:
    result = {}
    for horizon in HORIZONS_MS:
        column = f"signed_markout_{horizon}ms"
        result[f"average_{column}"] = (
            float(markouts[column].mean()) if column in markouts else np.nan
        )
    return result


def write_reports(
    *,
    output_dir: Path,
    artifact_root: Path,
    scenario_entries: list[dict[str, Any]],
    orders: pd.DataFrame,
    fills: pd.DataFrame,
    markouts: pd.DataFrame,
    signals_by_key: dict[tuple[str, str], int],
    input_info: dict[str, Any],
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    figures_dir = output_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)

    order_summary = summarize_orders(orders, signals_by_key)
    market_summary = summarize_market(orders, fills, markouts)
    passive_summary = summarize_passive(orders, fills, markouts)
    fill_latency = (
        fills.groupby(["mode", "model", "latency_ms"], dropna=False)
        .agg(
            fill_rows=("order_id", "count"),
            mean_implementation_shortfall=(
                "implementation_shortfall_vs_decision_mid",
                "mean",
            ),
            median_implementation_shortfall=(
                "implementation_shortfall_vs_decision_mid",
                "median",
            ),
        )
        .reset_index()
    )
    depth = (
        orders.groupby(["mode", "model", "latency_ms", "levels_consumed"], dropna=False)
        .size()
        .reset_index(name="order_count")
    )
    passive_fill = passive_summary.copy()
    adverse = (
        markouts.groupby(["mode", "model", "latency_ms"], dropna=False)
        .agg({f"signed_markout_{h}ms": "mean" for h in HORIZONS_MS})
        .reset_index()
    )
    runtime_summary = pd.DataFrame(
        [
            {
                "date": entry["date"],
                "model": entry["model"],
                "mode": entry["mode"],
                "latency_ms": entry["latency_ms"],
                "orders": entry["orders"],
                "fills": entry["fills"],
                "runtime_seconds": entry["runtime_seconds"],
            }
            for entry in scenario_entries
        ]
    )

    outputs = {
        "order_summary.csv": order_summary,
        "market_execution_summary.csv": market_summary,
        "passive_execution_summary.csv": passive_summary,
        "fill_latency_summary.csv": fill_latency,
        "depth_consumption_summary.csv": depth,
        "passive_fill_summary.csv": passive_fill,
        "adverse_selection_summary.csv": adverse,
        "runtime_summary.csv": runtime_summary,
    }
    for filename, frame in outputs.items():
        frame.to_csv(output_dir / filename, index=False)

    manifest_entries = [
        {
            key: value
            for key, value in entry.items()
            if key not in {"runtime_seconds"}
        }
        for entry in scenario_entries
    ]
    manifest_payload = {
        "artifact_identity": "phase11_execution_artifacts_v1",
        "phase11_execution_plan_hash": PHASE11_EXECUTION_PLAN_HASH,
        "phase11_execution_config_hash": PHASE11_EXECUTION_CONFIG_HASH,
        "phase10_signal_artifact_hash": PHASE10_SIGNAL_ARTIFACT_HASH,
        "entries": sorted(manifest_entries, key=lambda row: row["relative_dir"]),
    }
    execution_artifact_hash = hash_config(manifest_payload)
    execution_manifest = {
        "phase11_execution_artifact_hash": execution_artifact_hash,
        **manifest_payload,
    }
    (output_dir / "execution_manifest.json").write_text(
        json.dumps(execution_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    summary_without_hash = {
        **input_info,
        "phase11_status": "PASS",
        "real_data_dates": sorted(set(orders["date"])),
        "models": sorted(set(orders["model"])),
        "latency_scenarios_ms": sorted(int(value) for value in set(orders["latency_ms"])),
        "market_order_count": int((orders["mode"] == "market").sum()),
        "passive_order_count": int((orders["mode"] == "passive").sum()),
        "fill_count": int(len(fills)),
        "market_mean_shortfall_by_latency": market_summary[
            ["model", "latency_ms", "average_implementation_shortfall"]
        ].to_dict(orient="records"),
        "passive_fill_rate_by_latency": passive_summary[
            ["model", "latency_ms", "fill_rate", "mean_fill_fraction"]
        ].to_dict(orient="records"),
        "phase11_execution_artifact_hash": execution_artifact_hash,
    }
    write_readme(output_dir, summary_without_hash)
    write_figures(
        figures_dir=figures_dir,
        orders=orders,
        fills=fills,
        markouts=markouts,
        market_summary=market_summary,
        passive_summary=passive_summary,
        order_summary=order_summary,
    )
    results_hash = deterministic_results_hash(output_dir, summary_without_hash)
    final_summary = {**summary_without_hash, "phase11_results_hash": results_hash}
    (output_dir / "phase11_summary.json").write_text(
        json.dumps(final_summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return final_summary


def deterministic_results_hash(output_dir: Path, summary: dict[str, Any]) -> str:
    payload: dict[str, Any] = {}
    files = [
        "order_summary.csv",
        "market_execution_summary.csv",
        "passive_execution_summary.csv",
        "fill_latency_summary.csv",
        "depth_consumption_summary.csv",
        "passive_fill_summary.csv",
        "adverse_selection_summary.csv",
        "execution_manifest.json",
        "README.md",
    ]
    for filename in files:
        payload[filename] = (output_dir / filename).read_text(encoding="utf-8")
    payload["phase11_summary_without_results_hash"] = summary
    return hash_config(payload)


def write_readme(output_dir: Path, summary: dict[str, Any]) -> None:
    text = f"""# Phase 11 Execution Diagnostics

Phase 11 converts frozen Phase 10 desired directional states into deterministic
market and passive order/fill diagnostics. The run uses observation-time replay
ordering, explicit order-arrival timestamps, fixed quote-notional order sizing,
and no portfolio accounting.

- Execution plan hash: `{PHASE11_EXECUTION_PLAN_HASH}`
- Execution config hash: `{PHASE11_EXECUTION_CONFIG_HASH}`
- Phase 10 signal artifact hash: `{PHASE10_SIGNAL_ARTIFACT_HASH}`
- Real-data dates: `{", ".join(summary["real_data_dates"])}`
- Row-level artifact root: `/tmp/microalpha-phase11`

The real-data MVP uses `research_100ms.parquet` depth snapshots for book state
and Tardis raw trade prints for passive queue depletion. Markouts are computed
only after fills are frozen.
"""
    (output_dir / "README.md").write_text(text, encoding="utf-8")


def write_figures(
    *,
    figures_dir: Path,
    orders: pd.DataFrame,
    fills: pd.DataFrame,
    markouts: pd.DataFrame,
    market_summary: pd.DataFrame,
    passive_summary: pd.DataFrame,
    order_summary: pd.DataFrame,
) -> None:
    def save(name: str) -> None:
        plt.tight_layout()
        plt.savefig(figures_dir / name, dpi=140)
        plt.close()

    market_fills = fills[fills["mode"] == "market"]
    market_fills["implementation_shortfall_vs_decision_mid"].hist(bins=60)
    plt.title("Market order implementation shortfall")
    save("market_order_implementation_shortfall_distribution.png")

    market_summary.pivot(
        index="model",
        columns="latency_ms",
        values="average_implementation_shortfall",
    ).plot(kind="bar")
    plt.title("Market shortfall by latency")
    save("market_order_shortfall_by_latency.png")

    orders["levels_consumed"].hist(bins=range(0, int(orders["levels_consumed"].max()) + 2))
    plt.title("Depth levels consumed")
    save("depth_consumption_distribution.png")

    passive_summary.pivot(index="model", columns="latency_ms", values="fill_rate").plot(kind="bar")
    plt.title("Passive fill rate")
    save("passive_fill_rate_by_model_date.png")

    passive_orders = orders[orders["mode"] == "passive"]
    passive_orders["time_to_first_fill_ms"].dropna().hist(bins=50)
    plt.title("Passive time to first fill")
    save("passive_time_to_fill_distribution.png")

    passive_orders["fill_fraction"].hist(bins=50)
    plt.title("Passive fill fraction")
    save("passive_fill_fraction_distribution.png")

    markout_cols = [f"signed_markout_{h}ms" for h in HORIZONS_MS]
    markouts[markout_cols].mean().plot(kind="bar")
    plt.title("Post-fill signed markouts")
    save("post_fill_markouts.png")

    markouts.groupby("mode")[markout_cols].mean().T.plot(kind="bar")
    plt.title("Market vs passive signed markouts")
    save("market_vs_passive_markout_comparison.png")

    order_summary.groupby(["model", "mode"])["order_count"].sum().unstack().plot(kind="bar")
    plt.title("Order traffic by model")
    save("order_traffic_by_model.png")

    combined = pd.concat(
        [
            market_summary.assign(mode="market")[["model", "latency_ms", "fill_rate", "mode"]],
            passive_summary[["model", "latency_ms", "fill_rate"]].assign(mode="passive"),
        ]
    )
    combined.pivot_table(
        index="model",
        columns=["mode", "latency_ms"],
        values="fill_rate",
    ).plot(kind="bar")
    plt.title("Execution diagnostic fill rates")
    save("model_execution_diagnostics.png")


def main() -> None:
    args = parse_args()
    config = load_yaml_config(args.config)
    output_dir = Path(args.output_dir)
    artifact_root = Path(config["real_data_diagnostic"]["artifact_root"])
    if args.clean:
        if output_dir.exists():
            shutil.rmtree(output_dir)
        if artifact_root.exists():
            shutil.rmtree(artifact_root)
    input_info = verify_frozen_inputs(args, config)

    derived_root = Path(config["real_data_diagnostic"]["derived_root"])
    source_root = Path(config["real_data_diagnostic"]["source_root"])
    dates = [str(date) for date in config["real_data_diagnostic"]["dates"]]
    models = [str(model) for model in config["real_data_diagnostic"]["models"]]
    market_latencies = [
        int(value) for value in config["timing"]["market_order_latency_scenarios_ms"]
    ]
    passive_latencies = [
        int(value) for value in config["timing"]["passive_order_latency_scenarios_ms"]
    ]

    entries: list[dict[str, Any]] = []
    all_orders = []
    all_fills = []
    all_markouts = []
    signals_by_key: dict[tuple[str, str], int] = {}
    for date in dates:
        if date.startswith("2026-"):
            raise ValueError("Phase 11 must not access 2026 holdout dates")
        book_frame, book_times = read_book_frame(
            derived_root,
            date,
            int(config["market_orders"]["max_book_depth_levels"]),
        )
        trades, trade_times = read_trades(source_root, date)
        for model in models:
            signal_file = (
                Path(config["real_data_diagnostic"]["signal_root"])
                / f"date={date}"
                / f"model={model}"
                / "signals.parquet"
            )
            signal_frame = pd.read_parquet(signal_file, columns=["final_signal"])
            signals_by_key[(date, model)] = int((signal_frame["final_signal"] != 0).sum())
            for latency_ms in market_latencies:
                entries.append(
                    run_scenario(
                        date=date,
                        model=model,
                        mode="market",
                        latency_ms=latency_ms,
                        config=config,
                        book_frame=book_frame,
                        book_times=book_times,
                        trades=trades,
                        trade_times=trade_times,
                        output_root=artifact_root,
                    )
                )
            for latency_ms in passive_latencies:
                entries.append(
                    run_scenario(
                        date=date,
                        model=model,
                        mode="passive",
                        latency_ms=latency_ms,
                        config=config,
                        book_frame=book_frame,
                        book_times=book_times,
                        trades=trades,
                        trade_times=trade_times,
                        output_root=artifact_root,
                    )
                )

    for entry in entries:
        base = artifact_root / entry["relative_dir"]
        all_orders.append(pd.read_parquet(base / "orders.parquet"))
        all_fills.append(pd.read_parquet(base / "fills.parquet"))
        all_markouts.append(pd.read_parquet(base / "markouts.parquet"))

    orders = pd.concat(all_orders, ignore_index=True)
    fills = pd.concat(all_fills, ignore_index=True)
    markouts = pd.concat(all_markouts, ignore_index=True)
    fill_times = pd.to_datetime(fills["fill_time"], utc=True)
    arrival_times = pd.to_datetime(fills["order_arrival_time"], utc=True)
    if not (fill_times >= arrival_times).all():
        raise ValueError("Phase 11 invariant failed: fill before order arrival")
    summary = write_reports(
        output_dir=output_dir,
        artifact_root=artifact_root,
        scenario_entries=entries,
        orders=orders,
        fills=fills,
        markouts=markouts,
        signals_by_key=signals_by_key,
        input_info=input_info,
    )
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
