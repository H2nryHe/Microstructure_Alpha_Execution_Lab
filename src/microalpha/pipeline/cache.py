"""Deterministic cache manifests for per-day derived artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from microalpha.research.dataset import dataset_hash
from microalpha.utils.hashing import hash_config


def artifact_cache_key(
    *,
    stage: str,
    source_checksums: dict[str, str],
    config_hash: str,
    version: str,
) -> str:
    return hash_config(
        {
            "stage": stage,
            "source_checksums": source_checksums,
            "config_hash": config_hash,
            "version": version,
        }
    )


def artifact_manifest(
    *,
    stage: str,
    artifact_path: str | Path,
    source_checksums: dict[str, str],
    config_hash: str,
    version: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = Path(artifact_path)
    cache_key = artifact_cache_key(
        stage=stage,
        source_checksums=source_checksums,
        config_hash=config_hash,
        version=version,
    )
    return {
        "stage": stage,
        "artifact_path": str(path),
        "artifact_hash": dataset_hash(path) if path.exists() else "",
        "source_checksums": source_checksums,
        "config_hash": config_hash,
        "version": version,
        "cache_key": cache_key,
        "extra": extra or {},
    }


def cache_is_valid(
    manifest: dict[str, Any],
    *,
    artifact_path: str | Path,
    source_checksums: dict[str, str],
    config_hash: str,
    version: str,
    stage: str,
) -> bool:
    path = Path(artifact_path)
    if not path.exists():
        return False
    expected_key = artifact_cache_key(
        stage=stage,
        source_checksums=source_checksums,
        config_hash=config_hash,
        version=version,
    )
    if manifest.get("cache_key") != expected_key:
        return False
    if manifest.get("artifact_path") != str(path):
        return False
    return manifest.get("artifact_hash") == dataset_hash(path)
