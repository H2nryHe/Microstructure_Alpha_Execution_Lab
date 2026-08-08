"""Configuration validation helpers."""

from __future__ import annotations

from typing import Any


def validate_string_mapping_keys(value: Any, path: str = "config") -> None:
    """Require all mapping keys in a nested config object to be strings."""

    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError(
                    f"Non-string YAML mapping key detected at {path}: {key!r}. "
                    "Quote or rename reserved YAML keys."
                )
            validate_string_mapping_keys(child, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            validate_string_mapping_keys(item, f"{path}[{index}]")
