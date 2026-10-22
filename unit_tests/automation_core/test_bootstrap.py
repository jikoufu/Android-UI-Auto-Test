"""验证统一装配与配置定位，不使用真实设备或 AI 服务。"""

import pytest

from automation_core.ai.analyzer import AIAnalyzer
from automation_core.ai.client import AIClientError, OpenAICompatibleClient
from automation_core.bootstrap import create_adb, create_ai_executor, create_tv, load_device_config
from automation_core.config import PROJECT_ROOT, RUNTIME_ROOT, config_path


def test_default_paths_are_independent_of_working_directory(tmp_path, monkeypatch):
    """验证离开仓库工作目录后仍可找到配置和包内提示词。"""
    # Step 1：切换目录并从统一入口读取配置、创建离线对象。
    monkeypatch.chdir(tmp_path)
    settings = load_device_config()
    adb = create_adb(settings)
    analyzer = AIAnalyzer(object())

    # Step 2：配置、证据和提示词路径仍指向同一项目。
    assert config_path("devices.yaml") == PROJECT_ROOT / "configs/devices.yaml"
    assert settings["hierarchy_dump_attempts"] == 2
    assert adb.reports_dir == RUNTIME_ROOT / "evidence"
    assert analyzer.prompt_path == PROJECT_ROOT / "automation_core/ai/prompts/error_analysis.md"
    assert analyzer.prompt_path.is_file()


def test_device_factory_reuses_drivers_and_environment_serial(monkeypatch):
    """验证统一设备装配复用同一连接并允许环境覆盖配置。"""
    # Step 1：环境指定目标设备，配置中的 serial 不应覆盖它。
    monkeypatch.setenv("ANDROID_SERIAL", "env-device")
    monkeypatch.setenv("ADB_PATH", "custom-adb")
    tv = create_tv({"serial": "config-device", "command_timeout": 7})

    # Step 2：UI 与遥控器共享 ADB，默认日志与证据不写旧 reports。
    assert tv.adb.serial == "env-device"
    assert tv.adb.adb_path == "custom-adb"
    assert tv.ui.adb is tv.adb and tv.remote.adb is tv.adb
    executor = create_ai_executor(tv, analyzer=object())
    assert executor.trace_path == RUNTIME_ROOT / "logs/ai_recovery.jsonl"


@pytest.mark.parametrize("timeout", [0, -1, 301, float("nan"), float("inf")])
def test_factory_rejects_unbounded_device_timeout(timeout):
    """验证配置不能启用无界或非正设备超时。"""
    # Step 1：将无效超时传给设备入口，应在访问设备前拒绝。
    with pytest.raises(ValueError, match="command_timeout"):
        create_adb({"command_timeout": timeout})


def test_ai_credentials_are_required_only_on_explicit_creation(tmp_path, monkeypatch):
    """验证普通设备装配不依赖 AI 密钥，显式 AI 初始化清楚报缺失。"""
    # Step 1：从无密钥配置组装设备，不连接任何服务。
    monkeypatch.delenv("MISSING_TEST_PROVIDER_KEY", raising=False)
    config = tmp_path / "model.yaml"
    config.write_text("api_key_env: MISSING_TEST_PROVIDER_KEY\nmodel: offline-model\n", encoding="utf-8")
    tv = create_tv({"serial": "offline"})
    assert tv.adb is tv.ui.adb

    # Step 2：只有显式创建 AI 客户端才检查所需环境变量。
    with pytest.raises(AIClientError, match="MISSING_TEST_PROVIDER_KEY"):
        OpenAICompatibleClient.from_environment(config)
