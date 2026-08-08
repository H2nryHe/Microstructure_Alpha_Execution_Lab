from __future__ import annotations

from pathlib import Path

import pytest

from microalpha.config import load_config_bundle, load_yaml_config
from microalpha.utils.hashing import hash_config

yaml = pytest.importorskip("yaml")


def write_yaml(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_pyyaml_null_mapping_key_is_rejected(tmp_path: Path) -> None:
    assert yaml.safe_load("models:\n  null:\n    enabled: true\n") == {
        "models": {None: {"enabled": True}}
    }
    config_path = write_yaml(
        tmp_path / "model.yaml",
        "models:\n  null:\n    enabled: true\n",
    )

    with pytest.raises(
        ValueError,
        match=(
            r"Non-string YAML mapping key detected at model\.models: None\. "
            r"Quote or rename reserved YAML keys\."
        ),
    ):
        load_yaml_config(config_path)


def test_quoted_null_mapping_key_remains_string_and_hashes(tmp_path: Path) -> None:
    config_path = write_yaml(
        tmp_path / "model.yaml",
        'models:\n  "null":\n    enabled: true\n',
    )

    loaded = load_yaml_config(config_path)

    assert loaded == {"models": {"null": {"enabled": True}}}
    assert isinstance(hash_config(loaded), str)


def test_null_baseline_mapping_key_loads_and_hashes(tmp_path: Path) -> None:
    config_path = write_yaml(
        tmp_path / "model.yaml",
        "models:\n  null_baseline:\n    enabled: true\n",
    )

    loaded = load_yaml_config(config_path)

    assert loaded == {"models": {"null_baseline": {"enabled": True}}}
    assert isinstance(hash_config(loaded), str)


def test_nested_non_string_mapping_keys_are_rejected_recursively(tmp_path: Path) -> None:
    config_path = write_yaml(
        tmp_path / "model.yaml",
        "models:\n  ridge:\n    true:\n      enabled: true\n",
    )

    with pytest.raises(
        ValueError,
        match=(
            r"Non-string YAML mapping key detected at model\.models\.ridge: True\. "
            r"Quote or rename reserved YAML keys\."
        ),
    ):
        load_yaml_config(config_path)


def test_current_project_configs_load_and_hash_deterministically() -> None:
    first = load_config_bundle("configs")
    second = load_config_bundle("configs")

    assert first["model"]["models"]["null_baseline"]["enabled"] is True
    assert hash_config(first) == hash_config(second)
