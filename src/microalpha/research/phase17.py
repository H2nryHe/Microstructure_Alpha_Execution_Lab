"""Phase 17 release-readiness helpers."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from microalpha.config import load_yaml_config
from microalpha.utils.hashing import hash_config

PHASE17_PACKAGING_MANIFEST = Path("data/manifests/phase17_packaging_manifest.yaml")
RELEASE_VALIDATION = Path("reports/final/RELEASE_VALIDATION.json")
FORBIDDEN_HOLDOUT_YEAR = "2026"
PRIVATE_FINAL_FILES = [
    Path("reports/final/RESUME_BULLETS.md"),
    Path("reports/final/INTERVIEW_STORIES.md"),
]
RELEASE_HASH_INPUTS = [
    Path("README.md"),
    Path("REPRODUCIBILITY.md"),
    Path("DATA_GUIDE.md"),
    Path("RELEASE_CHECKLIST.md"),
    Path("reports/final/PROJECT_SUMMARY.md"),
    Path("reports/final/MICROSTRUCTURE_ALPHA_EXECUTION_LAB_REPORT.md"),
    Path("reports/final/FINAL_METRICS.json"),
    Path("reports/final/FINAL_ARTIFACT_INDEX.md"),
    Path("reports/phase16/PERFORMANCE_ENGINEERING.md"),
    Path("reports/phase16/README.md"),
    Path("reports/phase16/phase16_summary.json"),
    PHASE17_PACKAGING_MANIFEST,
]
PHASE17_MARKDOWN_LINK_FILES = [
    Path("README.md"),
    Path("REPRODUCIBILITY.md"),
    Path("DATA_GUIDE.md"),
    Path("RELEASE_CHECKLIST.md"),
    Path("reports/final/PROJECT_SUMMARY.md"),
    Path("reports/final/MICROSTRUCTURE_ALPHA_EXECUTION_LAB_REPORT.md"),
    Path("reports/final/FINAL_ARTIFACT_INDEX.md"),
    Path("reports/phase16/PERFORMANCE_ENGINEERING.md"),
    Path("reports/phase16/README.md"),
]


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def phase17_packaging_manifest_hash(
    path: str | Path = PHASE17_PACKAGING_MANIFEST,
) -> str:
    return hash_config(load_yaml_config(path))


def phase17_release_artifact_hash(root: str | Path = ".") -> str:
    base = Path(root)
    payload: dict[str, Any] = {"artifact": "phase17_public_release_artifacts_v1"}
    for path in RELEASE_HASH_INPUTS:
        payload[str(path)] = (base / path).read_text(encoding="utf-8")
    figures = base / "reports/final/figures"
    payload["final_figures"] = {
        path.name: file_sha256(path) for path in sorted(figures.glob("*.png"))
    }
    phase16_figures = base / "reports/phase16/figures"
    payload["phase16_figures"] = {
        path.name: file_sha256(path) for path in sorted(phase16_figures.glob("*.png"))
    }
    return hash_config(payload)


def private_files_present(root: str | Path = ".") -> list[str]:
    base = Path(root)
    return [str(path) for path in PRIVATE_FINAL_FILES if (base / path).exists()]


def markdown_link_issues(paths: list[Path] = PHASE17_MARKDOWN_LINK_FILES) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    pattern = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
    for path in paths:
        if not path.exists():
            issues.append({"source": str(path), "target": "missing-source"})
            continue
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            target = match.group(1).split("#", 1)[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            target_path = (path.parent / target).resolve()
            if not target_path.exists():
                issues.append({"source": str(path), "target": match.group(1)})
    return issues


def assert_no_2026_release_access(value: Any, path: str = "phase17") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            assert_no_2026_release_access(key, f"{path}.{key}")
            assert_no_2026_release_access(item, f"{path}.{key}")
        return
    if isinstance(value, list | tuple | set):
        for index, item in enumerate(value):
            assert_no_2026_release_access(item, f"{path}[{index}]")
        return
    if isinstance(value, str) and (
        value.startswith(f"{FORBIDDEN_HOLDOUT_YEAR}-")
        or f"/{FORBIDDEN_HOLDOUT_YEAR}-" in value
        or f"date={FORBIDDEN_HOLDOUT_YEAR}-" in value
    ):
        raise ValueError(f"Forbidden 2026 holdout access in {path}: {value}")


def load_release_validation(path: str | Path = RELEASE_VALIDATION) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
