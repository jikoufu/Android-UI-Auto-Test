"""供 CLI、pytest 和后端共用的惰性装配入口，不在导入时访问设备或 AI。"""

from __future__ import annotations

import math
import os
from typing import TYPE_CHECKING, Any

from automation_core.config import RUNTIME_ROOT, config_path, load_yaml
from automation_core.device.adb_client import ADBClient
from automation_core.device.device_manager import AndroidTV
from automation_core.device.uia2_client import UIDriver
from automation_core.remote.controller import RemoteController

if TYPE_CHECKING:
    from automation_core.ai.analyzer import AIAnalyzer
    from automation_core.ai.executor import AIExecutor
    from automation_core.evidence.collector import FailureContextCollector


def load_device_config() -> dict[str, Any]:
    """读取唯一的设备配置源。"""
    return load_yaml(config_path("devices.yaml"))


def load_model_config() -> dict[str, Any]:
    """读取非敏感模型与恢复配置。"""
    return load_yaml(config_path("models.yaml"))


def create_adb(settings: dict[str, Any] | None = None) -> ADBClient:
    """创建 ADB 客户端；首次操作时才访问设备。"""
    settings = load_device_config() if settings is None else settings
    timeout = float(settings.get("command_timeout", 15))
    if not math.isfinite(timeout) or timeout <= 0 or timeout > 300:
        raise ValueError("command_timeout must be between 0 and 300 seconds")
    return ADBClient(
        serial=os.getenv("ANDROID_SERIAL") or str(settings.get("serial") or "") or None,
        adb_path=os.getenv("ADB_PATH") or str(settings.get("adb_path") or "adb"),
        command_timeout=timeout,
        reports_dir=RUNTIME_ROOT / "evidence",
    )


def create_tv(settings: dict[str, Any] | None = None) -> AndroidTV:
    """组合 ADB、UI 与独立遥控器；不要求 pytest 或 FastAPI。"""
    settings = load_device_config() if settings is None else settings
    adb = create_adb(settings)
    ui = UIDriver(adb, hierarchy_dump_attempts=int(settings.get("hierarchy_dump_attempts", 2)))
    return AndroidTV(adb, RemoteController(adb), ui)


def create_ai_executor(
    tv: AndroidTV,
    *,
    settings: dict[str, Any] | None = None,
    analyzer: AIAnalyzer | None = None,
    context_collector: FailureContextCollector | None = None,
) -> AIExecutor:
    """为指定设备装配有界 Step 恢复；仅调用此工厂时读取 AI 凭证。"""
    from automation_core.ai.analyzer import AIAnalyzer
    from automation_core.ai.client import OpenAICompatibleClient
    from automation_core.ai.executor import AIExecutor
    from automation_core.ai.recovery import RecoveryManager
    from automation_core.evidence.collector import FailureContextCollector

    settings = load_model_config() if settings is None else settings
    if analyzer is None:
        analyzer = AIAnalyzer(OpenAICompatibleClient.from_environment())
    return AIExecutor(
        analyzer=analyzer,
        context_collector=context_collector if context_collector is not None else FailureContextCollector(tv, tv.ui),
        recovery_manager=RecoveryManager(max_steps=int(settings.get("max_recovery_steps", 3))),
        tv=tv,
        ui=tv.ui,
        min_recovery_confidence=float(settings.get("min_recovery_confidence", 0.75)),
        navigate_confidence=float(settings.get("navigate_confidence", 0.70)),
        back_confidence=float(settings.get("back_confidence", 0.55)),
        scroll_confidence=float(settings.get("scroll_confidence", 0.45)),
        trace_path=RUNTIME_ROOT / "logs" / "ai_recovery.jsonl",
    )
