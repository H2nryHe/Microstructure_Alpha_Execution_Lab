from __future__ import annotations

from pathlib import Path

import pytest

from microalpha.research.phase17 import (
    PHASE17_PACKAGING_MANIFEST,
    assert_no_2026_release_access,
    load_release_validation,
    markdown_link_issues,
    phase17_packaging_manifest_hash,
    phase17_release_artifact_hash,
    private_files_present,
)


def test_phase17_manifest_exists_and_rejects_holdout_dates() -> None:
    assert PHASE17_PACKAGING_MANIFEST.exists()
    assert len(phase17_packaging_manifest_hash()) == 64
    with pytest.raises(ValueError, match="Forbidden 2026"):
        assert_no_2026_release_access({"bad": "2026-01-01"})


def test_phase17_private_files_absent_and_links_resolve() -> None:
    assert private_files_present() == []
    assert markdown_link_issues() == []


def test_phase17_release_hash_is_deterministic() -> None:
    first = phase17_release_artifact_hash()
    second = phase17_release_artifact_hash()
    assert first == second
    assert len(first) == 64


def test_phase17_release_validation_records_hashes_when_present() -> None:
    path = Path("reports/final/RELEASE_VALIDATION.json")
    if not path.exists():
        pytest.skip("Phase 17 validation report has not been generated")
    validation = load_release_validation(path)
    assert validation["phase17_packaging_manifest_hash"] == phase17_packaging_manifest_hash()
    assert validation["phase17_release_artifact_hash"] == phase17_release_artifact_hash()
    assert validation["private_file_count_current_tree"] == 0
    assert validation["broken_link_count"] == 0
    assert validation["no_2026_access_confirmed"] is True
