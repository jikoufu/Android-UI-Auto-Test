"""Shared pytest fixtures. Device connections are opened only when used."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from devices import ADBClient, AndroidTV, RemoteController, UIDriver
from utils.config import load_yaml


ROOT = Path(__file__).resolve().parent


@pytest.fixture(scope="session")
def device_config() -> dict[str, object]:
    return load_yaml(ROOT / "config" / "device.yaml")


@pytest.fixture(scope="session")
def adb(device_config: dict[str, object]) -> ADBClient:
    return ADBClient(
        serial=os.getenv("ANDROID_SERIAL") or str(device_config.get("serial") or "") or None,
        adb_path=os.getenv("ADB_PATH", str(device_config.get("adb_path") or "adb")),
        command_timeout=float(device_config.get("command_timeout", 15)),
        reports_dir=str(device_config.get("reports_dir", "reports")),
    )


@pytest.fixture(scope="session")
def remote(adb: ADBClient) -> RemoteController:
    return RemoteController(adb)


@pytest.fixture(scope="session")
def ui(adb: ADBClient) -> UIDriver:
    """创建延迟连接的 UI 驱动，并复用 ADB fixture 选定的设备。"""
    return UIDriver(adb)


@pytest.fixture(scope="session")
def tv(adb: ADBClient, remote: RemoteController, ui: UIDriver) -> AndroidTV:
    return AndroidTV(adb, remote, ui)


@pytest.fixture(scope="session")
def device(tv: AndroidTV) -> AndroidTV:
    """Generic alias for tests that prefer the name ``device``."""
    return tv
