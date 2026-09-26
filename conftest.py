"""pytest 共享 fixture；设备连接在首次操作时才建立。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ai.analyzer import AIAnalyzer
from ai.client import OpenAICompatibleClient
from ai.context import FailureContextCollector
from ai.executor import AIExecutor
from ai.recovery import RecoveryManager
from devices import ADBClient, AndroidTV, RemoteController, UIDriver
from utils.config import load_yaml


ROOT = Path(__file__).resolve().parent


@pytest.fixture(scope="session")
def device_config() -> dict[str, object]:
    """读取项目设备配置。"""
    return load_yaml(ROOT / "config" / "device.yaml")


@pytest.fixture(scope="session")
def ai_config() -> dict[str, object]:
    """读取非敏感 AI 配置；凭证仍只从环境变量获取。"""
    return load_yaml(ROOT / "config" / "ai.yaml")


@pytest.fixture(scope="session")
def ai_client() -> OpenAICompatibleClient:
    """仅在测试请求此 fixture 时初始化 AI 客户端。"""
    return OpenAICompatibleClient.from_environment(ROOT / "config" / "ai.yaml")


@pytest.fixture(scope="session")
def ai_analyzer(ai_client: OpenAICompatibleClient) -> AIAnalyzer:
    """创建复用共享 AI 客户端的结构化失败分析器。"""
    return AIAnalyzer(ai_client, ROOT / "ai" / "prompts" / "error_analysis.md")


@pytest.fixture
def failure_context_collector(tv: AndroidTV, ui: UIDriver) -> FailureContextCollector:
    """创建复用当前设备 fixture 的失败现场采集器。"""
    return FailureContextCollector(tv, ui)


@pytest.fixture
def ai_executor(
    ai_analyzer: AIAnalyzer,
    failure_context_collector: FailureContextCollector,
    tv: AndroidTV,
    ui: UIDriver,
    ai_config: dict[str, object],
) -> AIExecutor:
    """创建复用既有分析器和设备驱动的 Step 执行器。"""
    return AIExecutor(
        analyzer=ai_analyzer,
        context_collector=failure_context_collector,
        recovery_manager=RecoveryManager(max_steps=int(ai_config.get("max_recovery_steps", 3))),
        tv=tv,
        ui=ui,
        min_recovery_confidence=float(ai_config.get("min_recovery_confidence", 0.75)),
        navigate_confidence=float(ai_config.get("navigate_confidence", 0.70)),
        back_confidence=float(ai_config.get("back_confidence", 0.55)),
        scroll_confidence=float(ai_config.get("scroll_confidence", 0.45)),
    )


@pytest.fixture(scope="session")
def adb(device_config: dict[str, object]) -> ADBClient:
    """创建共享 ADB 客户端并应用 serial、超时和报告目录配置。"""
    return ADBClient(
        serial=os.getenv("ANDROID_SERIAL") or str(device_config.get("serial") or "") or None,
        adb_path=os.getenv("ADB_PATH", str(device_config.get("adb_path") or "adb")),
        command_timeout=float(device_config.get("command_timeout", 15)),
        reports_dir=str(device_config.get("reports_dir", "reports")),
    )


@pytest.fixture(scope="session")
def remote(adb: ADBClient) -> RemoteController:
    """创建复用共享 ADB 连接的遥控器控制器。"""
    return RemoteController(adb)


@pytest.fixture(scope="session")
def ui(adb: ADBClient, device_config: dict[str, object]) -> UIDriver:
    """创建延迟连接的 UI 驱动，并复用 ADB fixture 选定的设备。"""
    return UIDriver(adb, hierarchy_dump_attempts=int(device_config.get("hierarchy_dump_attempts", 2)))


@pytest.fixture(scope="session")
def tv(adb: ADBClient, remote: RemoteController, ui: UIDriver) -> AndroidTV:
    """组合 ADB、遥控器和 UI 驱动为 Android TV 门面。"""
    return AndroidTV(adb, remote, ui)


@pytest.fixture(scope="session")
def device(tv: AndroidTV) -> AndroidTV:
    """提供通用设备 fixture 名称，兼容偏好 device 的测试。"""
    return tv
