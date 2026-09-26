from devices.adb import ADBClient


def test_current_activity_parses_miui_resumed_activity_format(monkeypatch):
    """验证小米系统 ResumedActivity 格式可解析出前台页面。"""
    adb = ADBClient(serial="fake-device")
    calls = []

    # Step 1：模拟 MIUI dumpsys 输出并记录短超时参数。
    def run_shell(command, timeout=None):
        calls.append((command, timeout))
        return "ResumedActivity: ActivityRecord{123 u0 com.android.settings/.MiuiSettings t20}"

    monkeypatch.setattr(adb, "run_shell", run_shell)

    # Step 2：确认 Activity 正确解析且调用沿用指定超时。
    assert adb.current_activity(timeout=2) == "com.android.settings/.MiuiSettings"
    assert calls == [("dumpsys activity activities", 2)]
