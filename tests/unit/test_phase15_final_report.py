from __future__ import annotations

import pytest

from microalpha.research.phase15 import (
    FINAL_ARTIFACT_INDEX,
    FINAL_MARKDOWN_FILES,
    FINAL_METRICS,
    FINAL_REPORT,
    README,
    disallowed_final_terms,
    load_final_metrics,
    markdown_link_issues,
    metric_claim_errors,
    missing_final_figures,
    phase15_final_report_hash,
    phase15_results_hash,
    unsupported_profit_claims,
)


def require_phase15_outputs() -> None:
    if not FINAL_REPORT.exists() or not FINAL_METRICS.exists():
        pytest.skip("Phase 15 final artifacts have not been generated")


def test_phase15_required_final_artifacts_exist() -> None:
    require_phase15_outputs()
    assert not missing_final_figures()
    for path in FINAL_MARKDOWN_FILES:
        assert path.exists(), path


def test_phase15_markdown_links_resolve() -> None:
    require_phase15_outputs()
    issues = markdown_link_issues()
    assert issues == []


def test_phase15_metric_claims_are_traceable() -> None:
    require_phase15_outputs()
    metrics = load_final_metrics()
    assert len(metrics) >= 25
    for metric in metrics:
        assert {
            "metric_name",
            "value",
            "unit",
            "source_phase",
            "source_file",
            "description",
        }.issubset(metric)
    errors = metric_claim_errors(
        metrics,
        [README, FINAL_REPORT],
    )
    assert errors == []


def test_phase15_hashes_are_deterministic_and_recorded() -> None:
    require_phase15_outputs()
    index = FINAL_ARTIFACT_INDEX.read_text(encoding="utf-8")
    report_hash = phase15_final_report_hash()
    results_hash = phase15_results_hash()
    assert report_hash in index
    assert results_hash in index


def test_phase15_no_holdout_paths_or_unsupported_terms() -> None:
    require_phase15_outputs()
    combined = "\n".join(path.read_text(encoding="utf-8") for path in FINAL_MARKDOWN_FILES)
    assert "2026 remains an untouched temporal holdout" in combined
    assert disallowed_final_terms(combined) == []
    assert unsupported_profit_claims(combined) == []


def test_readme_is_narrative_first_not_phase_first() -> None:
    require_phase15_outputs()
    text = README.read_text(encoding="utf-8")
    assert "## Research Question" in text
    assert "## Key Findings" in text
    assert "## Architecture" in text
    assert text.find("## Research Question") < text.find("## Reproduce")
    assert "## Phase 1" not in text[:2000]
