"""Configuration loading helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Union


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if value in {"null", "None", "~"}:
        return None
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value.strip("\"'")


def _normalized_lines(path: Path) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        lines.append((indent, raw_line.strip()))
    return lines


def _parse_block(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[Any, int]:
    if index >= len(lines):
        return {}, index

    current_indent, current_text = lines[index]
    if current_indent < indent:
        return {}, index

    if current_text.startswith("- "):
        values: list[Any] = []
        while index < len(lines):
            line_indent, text = lines[index]
            if line_indent != indent or not text.startswith("- "):
                break
            values.append(_parse_scalar(text[2:]))
            index += 1
        return values, index

    values: dict[str, Any] = {}
    while index < len(lines):
        line_indent, text = lines[index]
        if line_indent < indent:
            break
        if line_indent > indent:
            raise ValueError(f"Unexpected indentation while parsing YAML: {text}")
        if text.startswith("- "):
            break

        key, sep, value = text.partition(":")
        if not sep:
            raise ValueError(f"Unsupported YAML line: {text}")
        key = key.strip()
        value = value.strip()
        index += 1

        if value:
            values[key] = _parse_scalar(value)
            continue

        if index >= len(lines) or lines[index][0] <= line_indent:
            values[key] = {}
            continue

        child, index = _parse_block(lines, index, lines[index][0])
        values[key] = child
    return values, index


def _load_simple_yaml(path: Path) -> dict[str, Any]:
    """Fallback parser for the simple config files shipped with Phase 0.

    The project declares PyYAML as a dependency. This fallback keeps the smoke
    command usable in minimal local environments before dependencies are
    installed. It intentionally supports only the small subset used here.
    """

    parsed, final_index = _parse_block(_normalized_lines(path), 0, 0)
    if final_index != len(_normalized_lines(path)):
        raise ValueError(f"Could not parse entire YAML file: {path}")
    if not isinstance(parsed, dict):
        raise ValueError(f"Config file must contain a mapping: {path}")
    return parsed


def load_yaml_config(path: Union[str, Path]) -> dict[str, Any]:
    """Load a YAML config file into a dictionary."""

    config_path = Path(path)
    try:
        import yaml
    except ImportError:
        return _load_simple_yaml(config_path)

    with config_path.open("r", encoding="utf-8") as file:
        loaded = yaml.safe_load(file) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"Config file must contain a mapping: {config_path}")
    return loaded


def load_config_bundle(config_dir: Union[str, Path]) -> dict[str, Any]:
    """Load all YAML configs in a directory as a deterministic named bundle."""

    directory = Path(config_dir)
    if not directory.exists():
        raise FileNotFoundError(f"Config directory does not exist: {directory}")

    bundle: dict[str, Any] = {}
    for path in sorted(directory.glob("*.yaml")):
        bundle[path.stem] = load_yaml_config(path)
    return bundle
