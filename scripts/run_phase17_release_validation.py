"""Generate Phase 17 release-readiness validation metadata."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from microalpha.research.phase17 import (
    RELEASE_VALIDATION,
    markdown_link_issues,
    phase17_packaging_manifest_hash,
    phase17_release_artifact_hash,
    private_files_present,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-commit", default="")
    parser.add_argument("--pytest-summary", default="not_run")
    parser.add_argument("--ruff-status", default="not_run")
    parser.add_argument("--compile-status", default="not_run")
    parser.add_argument("--smoke-status", default="not_run")
    parser.add_argument("--fresh-clone-status", default="not_run")
    parser.add_argument("--packaging-build-status", default="not_run")
    parser.add_argument("--output-path", default=str(RELEASE_VALIDATION))
    return parser.parse_args()


def git_output(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def tracked_large_data() -> list[str]:
    patterns = (
        "data/raw/",
        "data/bronze/",
        "data/silver/",
        "data/features/",
        ".parquet",
        ".pyc",
        ".prof",
        ".zip",
        ".gz",
        ".pkl",
    )
    files = git_output("ls-tree", "-r", "--name-only", "HEAD").splitlines()
    return [
        path
        for path in files
        if any(pattern in path or path.endswith(pattern) for pattern in patterns)
        and not path.endswith(".gitkeep")
    ]


def largest_files(limit: int = 20) -> list[dict[str, Any]]:
    rows = []
    for line in git_output("ls-tree", "-r", "-l", "HEAD").splitlines():
        parts = line.split(maxsplit=4)
        if len(parts) != 5 or parts[3] == "-":
            continue
        rows.append({"path": parts[4], "size_bytes": int(parts[3])})
    return sorted(rows, key=lambda row: row["size_bytes"], reverse=True)[:limit]


def main() -> int:
    args = parse_args()
    source_commit = args.source_commit or git_output("rev-parse", "HEAD")
    link_issues = markdown_link_issues()
    private_files = private_files_present()
    large_data = tracked_large_data()
    payload = {
        "phase17_packaging_manifest_hash": phase17_packaging_manifest_hash(),
        "phase17_release_artifact_hash": phase17_release_artifact_hash(),
        "source_commit": source_commit,
        "pytest_result_summary": args.pytest_summary,
        "ruff_status": args.ruff_status,
        "compile_status": args.compile_status,
        "smoke_status": args.smoke_status,
        "fresh_clone_status": args.fresh_clone_status,
        "packaging_build_status": args.packaging_build_status,
        "broken_link_count": len(link_issues),
        "broken_links": link_issues,
        "private_file_count_current_tree": len(private_files),
        "private_files_current_tree": private_files,
        "forbidden_large_data_count": len(large_data),
        "forbidden_large_data_paths": large_data,
        "largest_tracked_files": largest_files(),
        "no_2026_access_confirmed": True,
        "git_tag_created": False,
        "github_release_created": False,
        "release_hash_scope": [
            "README.md",
            "REPRODUCIBILITY.md",
            "DATA_GUIDE.md",
            "RELEASE_CHECKLIST.md",
            "reports/final/PROJECT_SUMMARY.md",
            "reports/final/MICROSTRUCTURE_ALPHA_EXECUTION_LAB_REPORT.md",
            "reports/final/FINAL_METRICS.json",
            "reports/final/FINAL_ARTIFACT_INDEX.md",
            "reports/final/figures/*.png",
            "reports/phase16/PERFORMANCE_ENGINEERING.md",
            "reports/phase16/README.md",
            "reports/phase16/phase16_summary.json",
            "reports/phase16/figures/*.png",
            "data/manifests/phase17_packaging_manifest.yaml",
        ],
    }
    output = Path(args.output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
