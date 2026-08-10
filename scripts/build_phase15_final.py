"""Build Phase 15 final metrics, figures, and artifact index."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from microalpha.research.phase15 import (
    FINAL_ARTIFACT_INDEX,
    FINAL_FIGURE_DIR,
    FINAL_MARKDOWN_FILES,
    FINAL_METRICS,
    FINAL_REPORT,
    REQUIRED_FINAL_FIGURES,
    file_sha256,
    missing_final_figures,
    phase15_final_report_hash,
    phase15_results_hash,
)

PHASE14_COMMIT_SHA = "7290d86afa18b67fdf0c46b2eeea22253dab7bc1"
PHASE14_TESTS_RUN_ID = 31413110254
PHASE14_RESEARCH_SMOKE_RUN_ID = 31413111431


def read_json(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def metric(
    metric_name: str,
    value: Any,
    unit: str,
    source_phase: str,
    source_file: str,
    description: str,
    claim_strings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "metric_name": metric_name,
        "value": value,
        "unit": unit,
        "source_phase": source_phase,
        "source_file": source_file,
        "description": description,
        "claim_strings": claim_strings or [],
    }


def collect_metrics() -> list[dict[str, Any]]:
    p7 = read_json("reports/phase7/phase7_summary.json")
    p7_audit = read_json("reports/phase7/audit/audit_summary.json")
    p8 = read_json("reports/phase8/phase8_summary.json")
    p9 = read_json("reports/phase9/phase9_summary.json")
    p10 = read_json("reports/phase10/phase10_summary.json")
    p11 = read_json("reports/phase11/phase11_summary.json")
    p12 = pd.read_csv("reports/phase12/pnl_by_scenario.csv")
    p13 = pd.read_csv("reports/phase13/breakeven_costs.csv")
    p14 = read_json("reports/phase14/phase14_summary.json")
    p14_breakeven = pd.read_csv("reports/phase14/market_breakeven_by_date.csv")
    p14_dates = pd.read_csv("reports/phase14/market_date_level_summary.csv")
    p14_rank = pd.read_csv("reports/phase14/model_ranking_stability.csv")
    p14_inc = pd.read_csv("reports/phase14/incremental_economics_by_date.csv")
    p14_passive = pd.read_csv("reports/phase14/passive_multiday_results.csv")

    qi_1s = [
        row
        for row in p7["top_primary_by_mean_ic"]
        if row["feature"] == "qi_1" and row["horizon"] == "1s"
    ][0]
    p12_market_0 = p12[(p12["mode"] == "market") & (p12["latency_ms"] == 0)]
    p13_market_0 = p13[(p13["mode"] == "market") & (p13["latency_ms"] == 0)]

    def p12_bps(model: str) -> float:
        return float(
            p12_market_0[p12_market_0["model"] == model][
                "gross_pnl_bps_of_turnover"
            ].iloc[0]
        )

    def p13_breakeven(model: str) -> float:
        return float(
            p13_market_0[p13_market_0["model"] == model]["breakeven_fee_bps"].iloc[0]
        )

    def p14_mean_breakeven(model: str, latency_ms: int) -> float:
        return float(
            p14_breakeven[
                (p14_breakeven["model"] == model)
                & (p14_breakeven["latency_ms"] == latency_ms)
            ]["mean_breakeven_fee_bps"].iloc[0]
        )

    def p14_median_breakeven(model: str, latency_ms: int) -> float:
        return float(
            p14_breakeven[
                (p14_breakeven["model"] == model)
                & (p14_breakeven["latency_ms"] == latency_ms)
            ]["median_breakeven_fee_bps"].iloc[0]
        )

    def p14_positive_net_days(model: str, latency_ms: int, fee_bps: float) -> int:
        return int(
            p14_dates[
                (p14_dates["model"] == model)
                & (p14_dates["latency_ms"] == latency_ms)
                & (p14_dates["fee_bps"] == fee_bps)
            ]["positive_net_days"].iloc[0]
        )

    def rank_count(metric_name: str, model: str) -> int:
        matches = p14_rank[
            (p14_rank["metric"] == metric_name) & (p14_rank["model"] == model)
        ]
        return 0 if matches.empty else int(matches["first_place_count"].iloc[0])

    ext_minus_qi = p14_inc[
        (p14_inc["comparison"] == "Extended_minus_QI")
        & (p14_inc["latency_ms"] == 0)
        & (p14_inc["fee_bps"] == 0.25)
    ]["delta_net_pnl"]
    passive_fill = p14_passive.groupby(["model", "latency_ms"])["fill_rate"].mean()
    passive_inventory = p14_passive.groupby(["model", "latency_ms"])[
        "terminal_position"
    ].mean()

    metrics = [
        metric(
            "phase14_commit_sha",
            PHASE14_COMMIT_SHA,
            "git_sha",
            "Phase 14",
            "user acceptance / git rev-parse HEAD",
            "Exact accepted Phase 14 commit.",
            [PHASE14_COMMIT_SHA],
        ),
        metric(
            "phase14_tests_run_id",
            PHASE14_TESTS_RUN_ID,
            "github_actions_run_id",
            "Phase 14",
            "user acceptance",
            "GitHub Actions tests run for the accepted Phase 14 commit.",
            [str(PHASE14_TESTS_RUN_ID)],
        ),
        metric(
            "phase14_research_smoke_run_id",
            PHASE14_RESEARCH_SMOKE_RUN_ID,
            "github_actions_run_id",
            "Phase 14",
            "user acceptance",
            "GitHub Actions research-smoke run for the accepted Phase 14 commit.",
            [str(PHASE14_RESEARCH_SMOKE_RUN_ID)],
        ),
        metric(
            "l2_rows_2019_validation_day",
            6486542,
            "rows",
            "Phase 3",
            "STATUS.md",
            "Full-day Tardis L2 input rows for the 2019 validation day.",
            ["6.49M L2 rows"],
        ),
        metric(
            "completed_event_states_2019_validation_day",
            815980,
            "rows",
            "Phase 4",
            "STATUS.md",
            "Completed event-state rows for the 2019 validation day.",
            ["816K completed event states"],
        ),
        metric(
            "fixed_100ms_observations_2019_validation_day",
            863949,
            "rows",
            "Phase 4",
            "STATUS.md",
            "Fixed 100ms research observations for the 2019 validation day.",
            ["864K fixed 100ms observations"],
        ),
        metric(
            "feature_count",
            91,
            "features",
            "Phase 5",
            "STATUS.md",
            "Leakage-controlled feature columns generated for the research table.",
            ["91 leakage-controlled features", "91 features"],
        ),
        metric(
            "development_date_count",
            24,
            "dates",
            "Phase 7",
            "reports/phase7/phase7_summary.json",
            "Pre-registered 2024-2025 development dates.",
            ["24 development dates"],
        ),
        metric(
            "phase7_primary_observations",
            20735105,
            "paired_observations",
            "Phase 7",
            "reports/phase7/primary_ic.csv",
            "Primary QI 1s paired state observations across development dates.",
            ["~20.7M observations"],
        ),
        metric(
            "phase9_expanding_fold_count",
            p9["expanding_fold_count"],
            "folds",
            "Phase 9",
            "reports/phase9/phase9_summary.json",
            "Expanding walk-forward validation folds.",
            ["18 expanding folds"],
        ),
        metric(
            "phase7_qi_1s_mean_daily_spearman_ic",
            qi_1s["mean_spearman_ic"],
            "spearman_ic",
            "Phase 7",
            "reports/phase7/primary_ic.csv",
            "Mean daily Spearman IC for QI versus 1s forward return.",
            ["0.426 mean daily Spearman IC", "0.43 mean daily Spearman IC"],
        ),
        metric(
            "phase7_qi_1s_positive_days",
            qi_1s["positive_days"],
            "dates",
            "Phase 7",
            "reports/phase7/primary_ic.csv",
            "Development dates with positive QI 1s IC.",
            ["24/24 development dates positive"],
        ),
        metric(
            "phase7_changed_state_qi_ic",
            0.446883,
            "spearman_ic",
            "Phase 7 audit",
            "reports/phase7/audit/changed_state_ic.csv",
            "Mean changed-state QI 1s IC.",
            ["0.447 changed-state IC"],
        ),
        metric(
            "phase7_nonoverlap_qi_ic",
            0.425474,
            "spearman_ic",
            "Phase 7 audit",
            "reports/phase7/nonoverlap_ic.csv",
            "Mean non-overlap QI 1s IC.",
            ["0.425 non-overlap IC"],
        ),
        metric(
            "phase7_temporal_mismatch_control_ic",
            p7_audit["temporal_negative_control"]["mean_spearman_ic"],
            "spearman_ic",
            "Phase 7 audit",
            "reports/phase7/audit/audit_summary.json",
            "QI lagged by five minutes temporal mismatch control.",
            ["0.004 temporal mismatch IC"],
        ),
        metric(
            "phase8_qi_baseline_mean_daily_ic",
            p8["qi_baseline_mean_daily_ic"],
            "spearman_ic",
            "Phase 8",
            "reports/phase8/phase8_summary.json",
            "QI baseline mean daily IC in Phase 8 validation.",
            ["0.422 QI baseline mean daily IC"],
        ),
        metric(
            "phase8_extended_lightgbm_delta_ic",
            p8["top_incremental_lift"][0]["mean_delta_daily_ic"],
            "delta_spearman_ic",
            "Phase 8",
            "reports/phase8/phase8_summary.json",
            "Extended LightGBM mean daily IC lift over QI baseline.",
            ["+0.008 Extended LightGBM lift"],
        ),
        metric(
            "phase8_extended_positive_lift_months",
            p8["top_incremental_lift"][0]["positive_lift_days"],
            "months",
            "Phase 8",
            "reports/phase8/phase8_summary.json",
            "Validation months where Extended LightGBM improved over QI.",
            ["12/12 validation months"],
        ),
        metric(
            "phase9_qi_mean_ic",
            p9["qi_ic"]["mean"],
            "spearman_ic",
            "Phase 9",
            "reports/phase9/phase9_summary.json",
            "QI mean IC across expanding walk-forward folds.",
            ["0.432 QI mean IC"],
        ),
        metric(
            "phase9_qi_ofi_delta_ic",
            p9["expanding_delta_qi_ofi"]["mean_delta_ic"],
            "delta_spearman_ic",
            "Phase 9",
            "reports/phase9/phase9_summary.json",
            "QI+OFI mean IC lift over QI across expanding folds.",
            ["+0.0067 QI+OFI delta"],
        ),
        metric(
            "phase9_extended_delta_ic",
            p9["expanding_delta_extended"]["mean_delta_ic"],
            "delta_spearman_ic",
            "Phase 9",
            "reports/phase9/phase9_summary.json",
            "Extended model mean IC lift over QI across expanding folds.",
            ["+0.0107 Extended delta"],
        ),
        metric(
            "phase9_rolling6_extended_delta_ic",
            p9["rolling_delta_extended"]["mean_delta_ic"],
            "delta_spearman_ic",
            "Phase 9",
            "reports/phase9/phase9_summary.json",
            "Extended model rolling-6 mean IC lift over QI.",
            ["+0.0082 rolling-6 Extended delta"],
        ),
        metric(
            "phase10_qi_active_coverage",
            p10["active_coverage_mean_by_model"]["qi_direct_baseline"],
            "fraction",
            "Phase 10",
            "reports/phase10/phase10_summary.json",
            "QI primary q10/q90 active signal coverage.",
            ["21.7% QI active coverage"],
        ),
        metric(
            "phase10_qi_ofi_active_coverage",
            p10["active_coverage_mean_by_model"]["lightgbm_qi_ofi"],
            "fraction",
            "Phase 10",
            "reports/phase10/phase10_summary.json",
            "QI+OFI primary q10/q90 active signal coverage.",
            ["20.2% QI+OFI active coverage"],
        ),
        metric(
            "phase10_extended_active_coverage",
            p10["active_coverage_mean_by_model"]["lightgbm_extended"],
            "fraction",
            "Phase 10",
            "reports/phase10/phase10_summary.json",
            "Extended primary q10/q90 active signal coverage.",
            ["17.3% Extended active coverage"],
        ),
        metric(
            "phase11_passive_fill_rate_min",
            min(row["fill_rate"] for row in p11["passive_fill_rate_by_latency"]),
            "fraction",
            "Phase 11",
            "reports/phase11/phase11_summary.json",
            "Minimum reference-day passive fill rate across model/latency scenarios.",
            ["1.5% to 4.5% passive fill-rate range"],
        ),
        metric(
            "phase11_passive_fill_rate_max",
            max(row["fill_rate"] for row in p11["passive_fill_rate_by_latency"]),
            "fraction",
            "Phase 11",
            "reports/phase11/phase11_summary.json",
            "Maximum reference-day passive fill rate across model/latency scenarios.",
            ["1.5% to 4.5% passive fill-rate range"],
        ),
        metric(
            "phase12_qi_market_0ms_gross_bps",
            p12_bps("qi_direct_baseline"),
            "bps_of_turnover",
            "Phase 12",
            "reports/phase12/pnl_by_scenario.csv",
            "Reference-day QI market 0ms gross PnL per turnover.",
            ["0.376 bps QI market 0ms"],
        ),
        metric(
            "phase12_qi_ofi_market_0ms_gross_bps",
            p12_bps("lightgbm_qi_ofi"),
            "bps_of_turnover",
            "Phase 12",
            "reports/phase12/pnl_by_scenario.csv",
            "Reference-day QI+OFI market 0ms gross PnL per turnover.",
            ["0.252 bps QI+OFI market 0ms"],
        ),
        metric(
            "phase12_extended_market_0ms_gross_bps",
            p12_bps("lightgbm_extended"),
            "bps_of_turnover",
            "Phase 12",
            "reports/phase12/pnl_by_scenario.csv",
            "Reference-day Extended market 0ms gross PnL per turnover.",
            ["0.277 bps Extended market 0ms"],
        ),
        metric(
            "phase13_qi_reference_breakeven_bps",
            p13_breakeven("qi_direct_baseline"),
            "fee_bps",
            "Phase 13",
            "reports/phase13/breakeven_costs.csv",
            "Reference-day QI market 0ms breakeven fee.",
            ["0.376 bps QI reference breakeven"],
        ),
        metric(
            "phase13_qi_ofi_reference_breakeven_bps",
            p13_breakeven("lightgbm_qi_ofi"),
            "fee_bps",
            "Phase 13",
            "reports/phase13/breakeven_costs.csv",
            "Reference-day QI+OFI market 0ms breakeven fee.",
            ["0.253 bps QI+OFI reference breakeven"],
        ),
        metric(
            "phase13_extended_reference_breakeven_bps",
            p13_breakeven("lightgbm_extended"),
            "fee_bps",
            "Phase 13",
            "reports/phase13/breakeven_costs.csv",
            "Reference-day Extended market 0ms breakeven fee.",
            ["0.277 bps Extended reference breakeven"],
        ),
        metric(
            "phase14_primary_market_dates",
            len(p14["primary_market_dates"]),
            "dates",
            "Phase 14",
            "reports/phase14/phase14_summary.json",
            "Primary cross-date market robustness sample size.",
            ["six primary dates"],
        ),
        metric(
            "phase14_qi_mean_breakeven_0ms",
            p14_mean_breakeven("qi_direct_baseline", 0),
            "fee_bps",
            "Phase 14",
            "reports/phase14/market_breakeven_by_date.csv",
            "QI mean breakeven fee across six dates at 0ms.",
            ["0.687 bps QI mean breakeven at 0ms"],
        ),
        metric(
            "phase14_qi_median_breakeven_0ms",
            p14_median_breakeven("qi_direct_baseline", 0),
            "fee_bps",
            "Phase 14",
            "reports/phase14/market_breakeven_by_date.csv",
            "QI median breakeven fee across six dates at 0ms.",
            ["0.440 bps QI median breakeven at 0ms"],
        ),
        metric(
            "phase14_extended_mean_breakeven_0ms",
            p14_mean_breakeven("lightgbm_extended", 0),
            "fee_bps",
            "Phase 14",
            "reports/phase14/market_breakeven_by_date.csv",
            "Extended mean breakeven fee across six dates at 0ms.",
            ["0.652 bps Extended mean breakeven at 0ms"],
        ),
        metric(
            "phase14_qi_net_positive_days_025bps_0ms",
            p14_positive_net_days("qi_direct_baseline", 0, 0.25),
            "dates",
            "Phase 14",
            "reports/phase14/market_date_level_summary.csv",
            "QI net-positive days at 0.25 bps and 0ms.",
            ["3/6 QI net-positive days at 0.25 bps"],
        ),
        metric(
            "phase14_qi_net_positive_days_050bps_0ms",
            p14_positive_net_days("qi_direct_baseline", 0, 0.5),
            "dates",
            "Phase 14",
            "reports/phase14/market_date_level_summary.csv",
            "QI net-positive days at 0.50 bps and 0ms.",
            ["2/6 QI net-positive days at 0.50 bps"],
        ),
        metric(
            "phase14_qi_efficiency_first_place_count",
            rank_count("gross_pnl_bps_of_turnover", "qi_direct_baseline"),
            "rank_contexts",
            "Phase 14",
            "reports/phase14/model_ranking_stability.csv",
            "QI first-place count on gross bps per turnover.",
            ["8 first-place efficiency contexts"],
        ),
        metric(
            "phase14_extended_minus_qi_mean_delta_net_025bps_0ms",
            float(ext_minus_qi.mean()),
            "quote_currency",
            "Phase 14",
            "reports/phase14/incremental_economics_by_date.csv",
            "Extended minus QI mean net PnL delta at 0.25 bps and 0ms.",
            ["-2443.68 mean Extended-minus-QI net delta"],
        ),
        metric(
            "phase14_passive_qi_mean_fill_rate_0ms",
            float(passive_fill.loc[("qi_direct_baseline", 0)]),
            "fraction",
            "Phase 14",
            "reports/phase14/passive_multiday_results.csv",
            "QI passive mean fill rate across robustness dates at 0ms.",
            ["1.56% QI passive mean fill rate"],
        ),
        metric(
            "phase14_passive_extended_mean_fill_rate_0ms",
            float(passive_fill.loc[("lightgbm_extended", 0)]),
            "fraction",
            "Phase 14",
            "reports/phase14/passive_multiday_results.csv",
            "Extended passive mean fill rate across robustness dates at 0ms.",
            ["4.70% Extended passive mean fill rate"],
        ),
        metric(
            "phase14_passive_extended_mean_terminal_position_0ms",
            float(passive_inventory.loc[("lightgbm_extended", 0)]),
            "base_units",
            "Phase 14",
            "reports/phase14/passive_multiday_results.csv",
            "Extended passive mean terminal position across robustness dates at 0ms.",
            ["4.79 mean Extended terminal position"],
        ),
        metric(
            "phase14_plan_hash",
            p14["phase14_plan_hash"],
            "hash",
            "Phase 14",
            "reports/phase14/phase14_summary.json",
            "Frozen Phase 14 plan hash.",
            [p14["phase14_plan_hash"]],
        ),
        metric(
            "phase14_results_hash",
            p14["phase14_results_hash"],
            "hash",
            "Phase 14",
            "reports/phase14/phase14_summary.json",
            "Frozen Phase 14 results hash.",
            [p14["phase14_results_hash"]],
        ),
    ]
    return sorted(metrics, key=lambda row: row["metric_name"])


def write_metrics() -> None:
    FINAL_METRICS.parent.mkdir(parents=True, exist_ok=True)
    metrics = collect_metrics()
    FINAL_METRICS.write_text(
        json.dumps(metrics, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def copy_figure(source: str, name: str) -> None:
    shutil.copyfile(source, FINAL_FIGURE_DIR / name)


def save_architecture() -> None:
    labels = [
        "Tardis L2 + Trades",
        "Immutable Raw Data",
        "Market Data QA",
        "Order Book Replay",
        "Causal Research Dataset",
        "Microstructure Features",
        "Forward Labels",
        "Statistical Research",
        "Predictive Modeling",
        "Walk-Forward Evaluation",
        "Signal Construction",
        "Execution Simulator",
        "Accounting",
        "Cost / Latency Analysis",
        "Cross-Date Robustness",
    ]
    fig, ax = plt.subplots(figsize=(12, 7.5))
    ax.axis("off")
    columns = [labels[:5], labels[5:10], labels[10:]]
    x_positions = [0.16, 0.50, 0.84]
    for x, column in zip(x_positions, columns, strict=True):
        y_positions = list(reversed([0.12 + i * 0.17 for i in range(len(column))]))
        for y, label in zip(y_positions, column, strict=True):
            ax.text(
                x,
                y,
                label,
                ha="center",
                va="center",
                fontsize=10,
                bbox={
                    "boxstyle": "round,pad=0.35",
                    "facecolor": "#eef3f8",
                    "edgecolor": "#314355",
                    "linewidth": 1.1,
                },
            )
        for y0, y1 in zip(y_positions[:-1], y_positions[1:], strict=False):
            ax.annotate(
                "",
                xy=(x, y1 + 0.045),
                xytext=(x, y0 - 0.045),
                arrowprops={"arrowstyle": "->"},
            )
    for x0, x1, y in [(0.16, 0.50, 0.12), (0.50, 0.84, 0.12)]:
        ax.annotate("", xy=(x1 - 0.08, y), xytext=(x0 + 0.08, y), arrowprops={"arrowstyle": "->"})
    ax.set_title("Microstructure Alpha Execution Lab: Causal Research Pipeline", fontsize=14)
    fig.tight_layout()
    fig.savefig(FINAL_FIGURE_DIR / "architecture_diagram.png", dpi=160)
    plt.close(fig)


def save_signal_coverage() -> None:
    summary = read_json("reports/phase10/phase10_summary.json")
    names = ["QI", "QI+OFI", "Extended"]
    models = ["qi_direct_baseline", "lightgbm_qi_ofi", "lightgbm_extended"]
    coverage = [summary["active_coverage_mean_by_model"][model] * 100 for model in models]
    separation = [
        summary["long_short_separation_mean_by_model"][model] * 10000 for model in models
    ]
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    axes[0].bar(names, coverage, color="#386fa4")
    axes[0].set_ylabel("Active coverage (%)")
    axes[0].set_title("Primary q10/q90 coverage")
    axes[1].bar(names, separation, color="#b56576")
    axes[1].set_ylabel("Mean signed future-mid effect (bps)")
    axes[1].set_title("Conditional separation")
    for ax in axes:
        ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(FINAL_FIGURE_DIR / "signal_coverage_and_separation.png", dpi=160)
    plt.close(fig)


def save_market_gross_net() -> None:
    data = pd.read_csv("reports/phase13/market_fee_sensitivity.csv")
    subset = data[(data["latency_ms"] == 0) & (data["fee_bps"].isin([0.0, 0.25, 0.5]))]
    pivot = subset.pivot(index="model", columns="fee_bps", values="net_pnl_bps_of_turnover")
    pivot = pivot.loc[["qi_direct_baseline", "lightgbm_qi_ofi", "lightgbm_extended"]]
    pivot.index = ["QI", "QI+OFI", "Extended"]
    ax = pivot.plot(kind="bar", figsize=(8, 4), color=["#386fa4", "#f2cc8f", "#b56576"])
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_ylabel("Net PnL bps / turnover")
    ax.set_xlabel("")
    ax.set_title("Reference-day market economics under fee overlays")
    ax.legend(title="Fee bps")
    ax.spines[["top", "right"]].set_visible(False)
    plt.tight_layout()
    plt.savefig(FINAL_FIGURE_DIR / "market_gross_vs_net_economics.png", dpi=160)
    plt.close()


def save_qi_extended_efficiency() -> None:
    data = pd.read_csv("reports/phase14/market_date_level_summary.csv")
    subset = data[
        (data["latency_ms"] == 0)
        & (data["fee_bps"] == 0.0)
        & (data["model"].isin(["qi_direct_baseline", "lightgbm_extended"]))
    ].copy()
    subset["label"] = subset["model"].map(
        {"qi_direct_baseline": "QI", "lightgbm_extended": "Extended"}
    )
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar(subset["label"], subset["mean_daily_gross_bps"], color=["#386fa4", "#b56576"])
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_ylabel("Mean daily gross bps / turnover")
    ax.set_title("Phase 14 efficiency: QI vs Extended")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(FINAL_FIGURE_DIR / "qi_vs_extended_economic_efficiency.png", dpi=160)
    plt.close(fig)


def save_passive_tradeoff() -> None:
    data = pd.read_csv("reports/phase14/passive_multiday_results.csv")
    subset = data[data["latency_ms"] == 0]
    grouped = (
        subset.groupby("model")
        .agg(
            mean_fill_rate=("fill_rate", "mean"),
            mean_abs_terminal_position=("terminal_position", lambda values: values.abs().mean()),
        )
        .reset_index()
    )
    labels = {
        "qi_direct_baseline": "QI",
        "lightgbm_qi_ofi": "QI+OFI",
        "lightgbm_extended": "Extended",
    }
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.scatter(grouped["mean_fill_rate"] * 100, grouped["mean_abs_terminal_position"], s=90)
    for row in grouped.itertuples(index=False):
        ax.annotate(labels[row.model], (row.mean_fill_rate * 100, row.mean_abs_terminal_position))
    ax.set_xlabel("Mean fill rate (%)")
    ax.set_ylabel("Mean absolute terminal position")
    ax.set_title("Passive fill and inventory tradeoff")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(FINAL_FIGURE_DIR / "passive_fill_inventory_tradeoff.png", dpi=160)
    plt.close(fig)


def write_figures() -> None:
    FINAL_FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    save_architecture()
    copy_figure(
        "reports/phase7/figures/qi_1_1s_decile_curve.png",
        "qi_decile_future_1s_move.png",
    )
    copy_figure(
        "reports/phase7/figures/daily_ic_heatmap_chronological_dates.png",
        "daily_ic_stability.png",
    )
    copy_figure(
        "reports/phase9/figures/walkforward_ic_by_validation_date.png",
        "qi_vs_extended_walkforward_ic.png",
    )
    save_signal_coverage()
    save_market_gross_net()
    copy_figure(
        "reports/phase13/figures/market_net_pnl_vs_fee_bps_by_model.png",
        "pnl_turnover_vs_fee.png",
    )
    copy_figure(
        "reports/phase14/figures/breakeven_fee_by_date_model.png",
        "phase14_breakeven_distribution.png",
    )
    save_qi_extended_efficiency()
    save_passive_tradeoff()
    missing = missing_final_figures()
    if missing:
        raise FileNotFoundError(f"Missing final figures: {missing}")


def write_artifact_index() -> None:
    if not FINAL_REPORT.exists():
        raise FileNotFoundError(FINAL_REPORT)
    report_hash = phase15_final_report_hash()
    results_hash = phase15_results_hash()
    figure_hashes = {
        filename: file_sha256(FINAL_FIGURE_DIR / filename)
        for filename in REQUIRED_FINAL_FIGURES
    }
    lines = [
        "# Phase 15 Final Artifact Index",
        "",
        f"- phase15_final_report_hash: `{report_hash}`",
        f"- phase15_results_hash: `{results_hash}`",
        f"- accepted_phase14_commit: `{PHASE14_COMMIT_SHA}`",
        f"- phase14_tests_run_id: `{PHASE14_TESTS_RUN_ID}`",
        f"- phase14_research_smoke_run_id: `{PHASE14_RESEARCH_SMOKE_RUN_ID}`",
        "",
        "## Final Documents",
        "",
    ]
    for path in FINAL_MARKDOWN_FILES:
        if path == FINAL_ARTIFACT_INDEX:
            continue
        lines.append(f"- `{path}`")
    lines.extend(["", "## Curated Figures", ""])
    for filename in REQUIRED_FINAL_FIGURES:
        lines.append(f"- `reports/final/figures/{filename}`: `{figure_hashes[filename]}`")
    lines.extend(
        [
            "",
            "## Hash Scope",
            "",
            "The final report hash covers only the canonical final research report.",
            "The Phase 15 results hash covers README, final markdown documents,",
            "`FINAL_METRICS.json`, and SHA-256 identities of the curated figure files.",
            "Runtime, timestamps, and absolute paths are excluded.",
            "",
        ]
    )
    FINAL_ARTIFACT_INDEX.write_text("\n".join(lines), encoding="utf-8")
    print(
        json.dumps(
            {
                "phase15_final_report_hash": report_hash,
                "phase15_results_hash": results_hash,
            }
        )
    )


def main() -> None:
    write_metrics()
    write_figures()
    write_artifact_index()


if __name__ == "__main__":
    main()
