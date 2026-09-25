"""pytest 共享 fixture；设备连接在首次操作时才建立。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from devices import ADBClient, AndroidTV, RemoteController, UIDriver
from utils.config import load_yaml


ROOT = Path(__file__).resolve().parent


@pytest.fixture(scope="session")
def device_config() -> dict[str, object]:
    """读取项目设备配置。"""
    return load_yaml(ROOT / "config" / "device.yaml")


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
def ui(adb: ADBClient) -> UIDriver:
    """创建延迟连接的 UI 驱动，并复用 ADB fixture 选定的设备。"""
    return UIDriver(adb)


@pytest.fixture(scope="session")
def tv(adb: ADBClient, remote: RemoteController, ui: UIDriver) -> AndroidTV:
    """组合 ADB、遥控器和 UI 驱动为 Android TV 门面。"""
    return AndroidTV(adb, remote, ui)


@pytest.fixture(scope="session")
def device(tv: AndroidTV) -> AndroidTV:
    """提供通用设备 fixture 名称，兼容偏好 device 的测试。"""
    return tv
