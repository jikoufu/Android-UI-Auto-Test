"""Small helpers for loading repository YAML configuration safely."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_yaml(path: str | Path) -> dict[str, Any]:
    """Load a YAML mapping, returning an empty mapping for an empty file."""
    config_path = Path(path)
    if not config_path.exists():
        return {}
    with config_path.open("r", encoding="utf-8") as stream:
        value = yaml.safe_load(stream)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"Expected a YAML mapping in {config_path}")
    return value
