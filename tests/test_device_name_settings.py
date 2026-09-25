import pytest

from flows.settings_flow import SettingsFlow


@pytest.mark.device
def test_open_device_name_from_settings(tv, ui):
    """验证可以从系统设置进入“我的设备”并点击设备名称。"""
    settings = SettingsFlow(tv)

    # Step 1：打开系统设置并确认已进入设置页面。
    settings.open_settings()
    assert settings.is_settings_page()

    # Step 2：在设置首页点击“我的设备”。
    assert ui.click_text("我的设备", timeout=10)

    # Step 3：在“我的设备”页面点击“设备名称”。
    assert ui.click_text("设备名称", timeout=10)
