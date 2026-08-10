"""Phase 15 final-report validation and hashing helpers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from microalpha.utils.hashing import hash_config

FINAL_REPORT = Path("reports/final/MICROSTRUCTURE_ALPHA_EXECUTION_LAB_REPORT.md")
RESUME_BULLETS = Path("reports/final/RESUME_BULLETS.md")
INTERVIEW_STORIES = Path("reports/final/INTERVIEW_STORIES.md")
FINAL_METRICS = Path("reports/final/FINAL_METRICS.json")
FINAL_ARTIFACT_INDEX = Path("reports/final/FINAL_ARTIFACT_INDEX.md")
README = Path("README.md")
FINAL_FIGURE_DIR = Path("reports/final/figures")

FINAL_MARKDOWN_FILES = [
    README,
    FINAL_REPORT,
    RESUME_BULLETS,
    INTERVIEW_STORIES,
    FINAL_ARTIFACT_INDEX,
]

FINAL_HASH_INPUTS = [
    README,
    FINAL_REPORT,
    RESUME_BULLETS,
    INTERVIEW_STORIES,
    FINAL_METRICS,
]

REQUIRED_FINAL_FIGURES = [
    "architecture_diagram.png",
    "qi_decile_future_1s_move.png",
    "daily_ic_stability.png",
    "qi_vs_extended_walkforward_ic.png",
    "signal_coverage_and_separation.png",
    "market_gross_vs_net_economics.png",
    "pnl_turnover_vs_fee.png",
    "phase14_breakeven_distribution.png",
    "qi_vs_extended_economic_efficiency.png",
    "passive_fill_inventory_tradeoff.png",
]

HOLDOUT_YEAR = 2026
ALLOWED_PROFIT_CONTEXTS = [
    "not executable profit",
    "executable profit",
]


@dataclass(frozen=True)
class LinkIssue:
    source: str
    target: str


def load_final_metrics(path: Path = FINAL_METRICS) -> list[dict[str, object]]:
    return json.loads(path.read_text(encoding="utf-8"))


def metric_by_name(metrics: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {str(metric["metric_name"]): metric for metric in metrics}


def phase15_final_report_hash(report_path: Path = FINAL_REPORT) -> str:
    return hash_config(
        {
            "artifact": "phase15_final_report_v1",
            "report": report_path.read_text(encoding="utf-8"),
        }
    )


def file_sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def phase15_results_hash(root: Path = Path(".")) -> str:
    payload: dict[str, object] = {"artifact": "phase15_results_v1"}
    for path in FINAL_HASH_INPUTS:
        payload[str(path)] = (root / path).read_text(encoding="utf-8")
    payload["figures"] = {
        filename: file_sha256(root / FINAL_FIGURE_DIR / filename)
        for filename in REQUIRED_FINAL_FIGURES
    }
    return hash_config(payload)


def missing_final_figures(root: Path = Path(".")) -> list[str]:
    return [
        filename
        for filename in REQUIRED_FINAL_FIGURES
        if not (root / FINAL_FIGURE_DIR / filename).exists()
    ]


def markdown_link_issues(paths: list[Path] = FINAL_MARKDOWN_FILES) -> list[LinkIssue]:
    issues: list[LinkIssue] = []
    pattern = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
    for path in paths:
        if not path.exists():
            issues.append(LinkIssue(str(path), "missing-source"))
            continue
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            target = match.group(1).split("#", 1)[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            target_path = (path.parent / target).resolve()
            if not target_path.exists():
                issues.append(LinkIssue(str(path), match.group(1)))
    return issues


def unsupported_profit_claims(text: str) -> list[str]:
    hits: list[str] = []
    lower = text.lower()
    for match in re.finditer(r"\bprofit(?:able|ability)?\b", lower):
        window = lower[max(0, match.start() - 40) : match.end() + 40]
        if not any(allowed in window for allowed in ALLOWED_PROFIT_CONTEXTS):
            hits.append(window.strip())
    return hits


def disallowed_final_terms(text: str) -> list[str]:
    terms = []
    lower = text.lower()
    if f"{HOLDOUT_YEAR}/" in lower or f"{HOLDOUT_YEAR}-" in lower:
        terms.append("2026 path/date")
    if "annualized" in lower or "annualised" in lower:
        terms.append("annualized")
    if "sharpe" in lower:
        terms.append("sharpe")
    return terms


def metric_claim_errors(metrics: list[dict[str, object]], documents: list[Path]) -> list[str]:
    texts = {str(path): path.read_text(encoding="utf-8") for path in documents}
    errors: list[str] = []
    names = set()
    for metric in metrics:
        name = str(metric["metric_name"])
        if name in names:
            errors.append(f"duplicate metric_name: {name}")
        names.add(name)
        claim_strings = [str(value) for value in metric.get("claim_strings", [])]
        for claim in claim_strings:
            present = [path for path, text in texts.items() if claim in text]
            if not present:
                errors.append(f"claim string for {name!r} not found in final docs: {claim!r}")
    return errors
