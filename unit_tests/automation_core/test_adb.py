from automation_core.device.adb_client import ADBClient


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


def test_each_adb_fallback_dump_uses_a_unique_evidence_file(tmp_path, monkeypatch):
    """验证 ADB hierarchy 降级采集不会覆盖历史 XML。"""
    adb = ADBClient(serial="fake-device", reports_dir=tmp_path)
    monkeypatch.setattr(adb, "run_shell", lambda *_args, **_kwargs: '<hierarchy><node text="系统"/></hierarchy>')

    # Step 1：连续触发两次 ADB hierarchy 证据采集。
    first = adb.dump_ui()
    second = adb.dump_ui()

    # Step 2：确认两份唯一文件均保留 XML 内容。
    assert first != second
    assert first.exists() and second.exists()
    assert "系统" in first.read_text(encoding="utf-8")
    assert "系统" in second.read_text(encoding="utf-8")
