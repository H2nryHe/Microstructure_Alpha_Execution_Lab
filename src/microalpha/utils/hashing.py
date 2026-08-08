"""Stable hashing helpers for configuration and research artifacts."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any


def _json_default(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat(timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def canonical_json(value: Any) -> str:
    """Serialize a value into deterministic JSON."""

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=_json_default,
    )


def hash_config(config: dict[str, Any]) -> str:
    """Return a SHA-256 hash for a configuration dictionary."""

    encoded = canonical_json(config).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
