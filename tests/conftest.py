"""设备场景的 pytest 装配；核心初始化由 automation_core 提供。"""

from __future__ import annotations

import pytest

from automation_core.ai.analyzer import AIAnalyzer
from automation_core.ai.client import OpenAICompatibleClient
from automation_core.ai.executor import AIExecutor
from automation_core.bootstrap import create_ai_executor, create_tv, load_device_config, load_model_config
from automation_core.device.adb_client import ADBClient
from automation_core.device.device_manager import AndroidTV
from automation_core.device.uia2_client import UIDriver
from automation_core.evidence.collector import FailureContextCollector
from automation_core.remote.controller import RemoteController


@pytest.fixture(scope="session")
def device_config() -> dict[str, object]:
    """读取统一设备配置。"""
    return load_device_config()


@pytest.fixture(scope="session")
def ai_config() -> dict[str, object]:
    """读取非敏感 AI 配置。"""
    return load_model_config()


@pytest.fixture(scope="session")
def tv(device_config: dict[str, object]) -> AndroidTV:
    """惰性组装 Android TV；首次设备操作时连接。"""
    return create_tv(device_config)


@pytest.fixture(scope="session")
def adb(tv: AndroidTV) -> ADBClient:
    """复用同一设备的 ADB 客户端。"""
    return tv.adb


@pytest.fixture(scope="session")
def remote(tv: AndroidTV) -> RemoteController:
    """复用同一设备的遥控器。"""
    return tv.remote


@pytest.fixture(scope="session")
def ui(tv: AndroidTV) -> UIDriver:
    """复用同一 serial 的 UI 驱动。"""
    return tv.ui


@pytest.fixture(scope="session")
def device(tv: AndroidTV) -> AndroidTV:
    """通用设备 fixture 别名。"""
    return tv


@pytest.fixture(scope="session")
def ai_client() -> OpenAICompatibleClient:
    """仅在测试请求该 fixture 时读取 AI 凭证。"""
    return OpenAICompatibleClient.from_environment()


@pytest.fixture(scope="session")
def ai_analyzer(ai_client: OpenAICompatibleClient) -> AIAnalyzer:
    """复用结构化分析器和包内提示词。"""
    return AIAnalyzer(ai_client)


@pytest.fixture
def failure_context_collector(tv: AndroidTV) -> FailureContextCollector:
    """每个测试独立缓存设备状态并采集现场。"""
    return FailureContextCollector(tv, tv.ui)


@pytest.fixture
def ai_executor(
    ai_analyzer: AIAnalyzer,
    failure_context_collector: FailureContextCollector,
    tv: AndroidTV,
    ai_config: dict[str, object],
) -> AIExecutor:
    """pytest 只管理生命周期，不持有核心能力的唯一入口。"""
    return create_ai_executor(
        tv,
        settings=ai_config,
        analyzer=ai_analyzer,
        context_collector=failure_context_collector,
    )
