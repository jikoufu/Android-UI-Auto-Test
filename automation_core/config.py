"""安全读取项目 YAML 配置的通用辅助函数。"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import os

import yaml


PROJECT_ROOT = Path(os.getenv("TV_AUTOMATION_HOME") or Path(__file__).resolve().parents[1]).resolve()


def resolve_project_path(path: str | Path) -> Path:
    """相对路径统一从项目根解析，不随命令行工作目录改变。"""
    value = Path(path)
    return (value if value.is_absolute() else PROJECT_ROOT / value).resolve()


RUNTIME_ROOT = resolve_project_path(os.getenv("TV_AUTOMATION_RUNTIME_DIR") or "runtime")


def config_path(name: str) -> Path:
    """返回唯一配置目录中的文件路径。"""
    if Path(name).name != name or name in {".", ".."}:
        raise ValueError("Configuration name must be a file name")
    return PROJECT_ROOT / "configs" / name


def load_yaml(path: str | Path) -> dict[str, Any]:
    """读取 YAML 字典；空文件或不存在的文件返回空字典。"""
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
