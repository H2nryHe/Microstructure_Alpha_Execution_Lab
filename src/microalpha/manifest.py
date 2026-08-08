"""Run manifest generation."""

from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional, Union

from microalpha.utils.hashing import hash_config
from microalpha.utils.time import utc_now_iso


@dataclass(frozen=True)
class RunManifest:
    run_id: str
    timestamp: str
    git_commit: str
    config_hash: str
    data_snapshot: str
    instrument: str
    date_range: dict[str, Any]
    feature_version: str
    label_version: str
    model_version: str
    execution_version: str
    random_seed: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def current_git_commit(repo_dir: Union[str, Path] = ".") -> str:
    """Return the current git commit or UNKNOWN outside a git checkout."""

    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_dir,
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return "UNKNOWN"
    return completed.stdout.strip()


def build_run_manifest(
    *,
    config: dict[str, Any],
    repo_dir: Union[str, Path] = ".",
    timestamp: Optional[str] = None,
) -> RunManifest:
    """Build a manifest from a loaded configuration bundle."""

    experiment_config = config.get("experiment", {})
    model_config = config.get("model", {})
    feature_config = config.get("features", {})
    label_config = config.get("labels", {})
    execution_config = config.get("execution", {})

    manifest_timestamp = timestamp or utc_now_iso()
    config_digest = hash_config(config)
    run_id = f"{experiment_config.get('experiment_id', 'run')}-{config_digest[:12]}"

    return RunManifest(
        run_id=run_id,
        timestamp=manifest_timestamp,
        git_commit=current_git_commit(repo_dir),
        config_hash=config_digest,
        data_snapshot=str(experiment_config.get("data_snapshot", "")),
        instrument=str(experiment_config.get("instrument", "")),
        date_range=dict(experiment_config.get("date_range", {}) or {}),
        feature_version=str(
            experiment_config.get("feature_version", feature_config.get("feature_version", ""))
        ),
        label_version=str(
            experiment_config.get("label_version", label_config.get("label_version", ""))
        ),
        model_version=str(
            experiment_config.get("model_version", model_config.get("model_version", ""))
        ),
        execution_version=str(
            experiment_config.get(
                "execution_version", execution_config.get("execution_version", "")
            )
        ),
        random_seed=int(experiment_config.get("random_seed", model_config.get("random_seed", 0))),
    )


def write_manifest(manifest: RunManifest, path: Union[str, Path]) -> Path:
    """Write a manifest as YAML when possible, otherwise simple key-value YAML."""

    manifest_path = Path(path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_dict = manifest.to_dict()

    try:
        import yaml
    except ImportError:
        lines = []
        for key, value in manifest_dict.items():
            if isinstance(value, dict):
                lines.append(f"{key}:")
                for nested_key, nested_value in value.items():
                    rendered = "null" if nested_value is None else nested_value
                    lines.append(f"  {nested_key}: {rendered}")
            else:
                rendered = "null" if value is None else value
                lines.append(f"{key}: {rendered}")
        manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return manifest_path

    with manifest_path.open("w", encoding="utf-8") as file:
        yaml.safe_dump(manifest_dict, file, sort_keys=True)
    return manifest_path
