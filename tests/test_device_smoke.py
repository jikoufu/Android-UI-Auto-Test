from pathlib import Path

import pytest

from devices.adb import ADBError


@pytest.mark.device
def test_android_tv_basic_control(tv, ui):
    """验证 Android TV 的 ADB、uiautomator2、返回主页、hierarchy 和截图。"""
    # 确认 ADB 已连接设备，并能读取 Android 版本信息。
    try:
        state = tv.get_device_state()
    except ADBError as exc:
        # 无设备时跳过真实设备检查，ADB 配置或连接错误仍保留为失败。
        if "No Android device is connected" not in str(exc):
            raise
        pytest.skip("未检测到在线 Android 设备；连接设备后可运行此 smoke test。")
    assert state.serial
    assert state.android_version

    print(f"serial: {state.serial}")
    print(f"model: {state.model}")
    print(f"android: {state.android_version}")

    # 建立 uiautomator2 连接，再将设备返回主屏。
    device = ui.connect()
    assert device is not None
    ui.home()

    # 检查 UI hierarchy 已成功保存。
    xml_path = ui.dump_ui()
    assert isinstance(xml_path, Path)
    assert xml_path.exists()
    assert xml_path.stat().st_size > 0

    # 检查设备截图已成功保存。
    screenshot_path = ui.take_screenshot()
    assert isinstance(screenshot_path, Path)
    assert screenshot_path.exists()
    assert screenshot_path.stat().st_size > 0
