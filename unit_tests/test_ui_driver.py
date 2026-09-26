from __future__ import annotations

from http.client import RemoteDisconnected
import json
from json import JSONDecodeError
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai.tools.ui_tool import ui_tools
from devices.ui import DeviceTransportError, UIDriver, UIDriverError


class FakeSelector:
    info = {"text": "Network", "className": "android.widget.TextView"}

    def exists(self, timeout=0):
        """模拟元素存在性查询。"""
        return timeout <= 2

    def wait(self, timeout=10):
        """模拟等待元素出现。"""
        return timeout <= 2

    def click(self, timeout=10):
        """模拟一次成功的元素点击。"""
        return None


class FakeDevice:
    def __init__(self):
        """初始化用于记录调用的假设备。"""
        self.selectors = []
        self.pressed = []
        self.swipes = []

    def __call__(self, **selector):
        """记录定位条件并返回假元素。"""
        self.selectors.append(selector)
        return FakeSelector()

    def press(self, key):
        """记录发送的按键。"""
        self.pressed.append(key)

    def swipe_ext(self, direction, scale=0.7):
        """记录按方向滚动屏幕。"""
        self.swipes.append((direction, scale))

    def dump_hierarchy(self):
        """返回一段固定的 UI hierarchy XML。"""
        return '<hierarchy><node text="Network" /></hierarchy>'


class FakeADB:
    def __init__(self, reports_dir: Path):
        """提供测试用报告目录和设备 serial。"""
        self.reports_dir = reports_dir
        self.device_serial = "192.168.1.20:5555"
        self.dump_path: Path | None = None
        self.dump_error: Exception | None = None
        self.dump_calls = 0

    def dump_ui(self) -> Path:
        """记录 ADB hierarchy 降级调用。"""
        self.dump_calls += 1
        if self.dump_error is not None:
            raise self.dump_error
        if self.dump_path is None:
            raise RuntimeError("ADB dump was not configured")
        return self.dump_path


def test_connect_uses_adb_selected_serial(tmp_path, monkeypatch):
    """验证 UI 驱动沿用 ADB 选定的设备 serial。"""
    device = FakeDevice()
    connected_serials = []
    fake_module = SimpleNamespace(connect=lambda serial: (connected_serials.append(serial), device)[1])
    monkeypatch.setattr("devices.ui.u2", fake_module)

    driver = UIDriver(FakeADB(tmp_path))

    assert driver.connect() is device
    assert connected_serials == ["192.168.1.20:5555"]


def test_live_ui_actions_use_uiautomator_selectors(tmp_path):
    """验证查找、等待、点击和按键均委托给 UI 后端。"""
    device = FakeDevice()
    driver = UIDriver(FakeADB(tmp_path), device=device)

    assert driver.exists("text", "Network")
    assert driver.find_element("text", "Network")["text"] == "Network"
    assert driver.wait_exists("text", "Network", timeout=2)
    assert driver.click_text("Network")
    driver.back()
    driver.home()
    driver.scroll("down")

    assert device.selectors == [{"text": "Network"}] * 4
    assert device.pressed == ["back", "home"]
    assert device.swipes == [("up", 0.7)]


def test_click_visible_label_falls_back_to_content_description(tmp_path, monkeypatch):
    """验证无障碍描述标签也能通过统一方法点击。"""
    driver = UIDriver(FakeADB(tmp_path), device=FakeDevice())
    checks = []
    clicks = []

    # Step 1：模拟当前页面只存在 content description 标签。
    monkeypatch.setattr(
        driver,
        "exists",
        lambda attribute, value, timeout=0: checks.append((attribute, value)) or attribute == "description",
    )
    monkeypatch.setattr(
        driver,
        "click",
        lambda attribute, value, timeout=10: clicks.append((attribute, value, timeout)) or True,
    )

    # Step 2：确认统一点击方法在 text 未找到后改用 description。
    assert driver.click_visible_label("系统设置", timeout=3)
    assert checks == [("text", "系统设置"), ("description", "系统设置")]
    assert clicks == [("description", "系统设置", 3)]


def test_dump_and_saved_xml_inspection(tmp_path):
    """验证 hierarchy、截图保存和 XML 离线查找。"""
    device = FakeDevice()
    device.screenshot = lambda: SimpleNamespace(save=lambda path: Path(path).write_bytes(b"image"))
    driver = UIDriver(FakeADB(tmp_path), device=device)

    dump_path = driver.dump_ui()
    result = driver.find_element("text", "Network", xml_path=dump_path)
    screenshot_path = driver.take_screenshot()

    assert dump_path.read_text(encoding="utf-8").startswith("<hierarchy>")
    assert result == {"text": "Network"}
    assert screenshot_path.read_bytes() == b"image"


def test_dump_success_does_not_reconnect_or_fallback(tmp_path, monkeypatch):
    """验证正常 hierarchy 采集不触发重连或 ADB 降级。"""
    adb = FakeADB(tmp_path)
    device = FakeDevice()
    dump_calls = 0
    reconnect_calls = 0

    def dump_hierarchy() -> str:
        nonlocal dump_calls
        dump_calls += 1
        return "<hierarchy />"

    def connect(_serial: str):
        nonlocal reconnect_calls
        reconnect_calls += 1
        return FakeDevice()

    device.dump_hierarchy = dump_hierarchy
    monkeypatch.setattr("devices.ui.u2.connect", connect)
    driver = UIDriver(adb, device=device)

    # Step 1：用现有连接执行一次正常 hierarchy 采集。
    driver.dump_ui()

    # Step 2：确认未重连、未降级，并记录 u2 证据来源。
    assert dump_calls == 1
    assert reconnect_calls == 0
    assert adb.dump_calls == 0
    assert driver.last_dump_backend == "uiautomator2"
    assert driver.last_transport_recovery is None


@pytest.mark.parametrize("error", [RemoteDisconnected("remote closed"), JSONDecodeError("invalid", "", 0)])
def test_dump_reconnects_after_transport_failure(tmp_path, monkeypatch, error):
    """验证通信断开或无效 JSON 后丢弃旧连接并重试一次。"""
    adb = FakeADB(tmp_path)
    old_device = FakeDevice()
    new_device = FakeDevice()
    old_calls = 0
    new_calls = 0
    connected_serials = []

    def old_dump() -> str:
        nonlocal old_calls
        old_calls += 1
        raise error

    def new_dump() -> str:
        nonlocal new_calls
        new_calls += 1
        return '<hierarchy><node text="系统"/></hierarchy>'

    old_device.dump_hierarchy = old_dump
    new_device.dump_hierarchy = new_dump
    monkeypatch.setattr("devices.ui.u2.connect", lambda serial: (connected_serials.append(serial), new_device)[1])
    driver = UIDriver(adb, device=old_device)

    # Step 1：让旧连接采集失败，由驱动使用同一 serial 建立新连接。
    dump_path = driver.dump_ui()

    # Step 2：确认只重连一次，新连接完成采集且没有 ADB 降级。
    assert connected_serials == [adb.device_serial]
    assert old_calls == new_calls == 1
    assert adb.dump_calls == 0
    assert driver._device is new_device
    assert driver.last_dump_backend == "uiautomator2"
    assert driver.last_transport_recovery["u2_retry_success"] is True
    assert 'text="系统"' in dump_path.read_text(encoding="utf-8")


def test_dump_uses_adb_after_reconnected_u2_fails(tmp_path, monkeypatch):
    """验证重连后仍断开时调用现有 ADB dump 取得证据。"""
    adb = FakeADB(tmp_path)
    adb.dump_path = tmp_path / "adb.xml"
    adb.dump_path.write_text('<hierarchy><node text="系统" clickable="true"/></hierarchy>', encoding="utf-8")
    first = FakeDevice()
    second = FakeDevice()
    first.dump_hierarchy = lambda: (_ for _ in ()).throw(RemoteDisconnected("closed"))
    second.dump_hierarchy = lambda: (_ for _ in ()).throw(ConnectionResetError("reset"))
    monkeypatch.setattr("devices.ui.u2.connect", lambda _serial: second)
    driver = UIDriver(adb, device=first)

    # Step 1：让新旧 u2 连接均失败，触发 ADB 降级。
    dump_path = driver.dump_ui()

    # Step 2：确认返回 ADB XML 并保存恢复路径元数据。
    assert dump_path == adb.dump_path
    assert adb.dump_calls == 1
    assert driver.last_dump_backend == "adb"
    assert driver.last_transport_recovery["adb_fallback_used"] is True
    assert driver.last_transport_recovery["adb_fallback_success"] is True


def test_dump_raises_transport_error_when_all_backends_fail(tmp_path, monkeypatch):
    """验证 u2 重连与 ADB 降级全失败时抛出明确通信异常。"""
    adb = FakeADB(tmp_path)
    adb.dump_error = TimeoutError("adb timeout")
    device = FakeDevice()
    device.dump_hierarchy = lambda: (_ for _ in ()).throw(RemoteDisconnected("closed"))
    monkeypatch.setattr("devices.ui.u2.connect", lambda _serial: device)
    driver = UIDriver(adb, device=device)

    # Step 1：模拟两次 u2 失败和一次 ADB 失败。
    with pytest.raises(DeviceTransportError, match="after uiautomator2 reconnect and ADB fallback"):
        driver.dump_ui()

    # Step 2：确认达到上限后停止，且元数据如实记录失败。
    assert adb.dump_calls == 1
    assert driver.last_dump_backend is None
    assert driver.last_transport_recovery["adb_fallback_success"] is False


def test_dump_does_not_reconnect_for_non_transport_error(tmp_path, monkeypatch):
    """验证代码或参数错误不会被误当作设备断连。"""
    adb = FakeADB(tmp_path)
    device = FakeDevice()
    device.dump_hierarchy = lambda: (_ for _ in ()).throw(ValueError("bad argument"))
    reconnect_calls = 0

    def connect(_serial: str):
        nonlocal reconnect_calls
        reconnect_calls += 1
        return FakeDevice()

    monkeypatch.setattr("devices.ui.u2.connect", connect)
    driver = UIDriver(adb, device=device)

    # Step 1：让 hierarchy 操作抛出非通信异常。
    with pytest.raises(UIDriverError, match="ValueError"):
        driver.dump_ui()

    # Step 2：确认没有重连或 ADB 降级。
    assert reconnect_calls == 0
    assert adb.dump_calls == 0
    assert driver.last_transport_recovery is None


def test_click_transport_failure_is_not_reported_as_business_error(tmp_path):
    """验证点击时的连接断开被归类为设备通信异常。"""
    class DisconnectedSelector(FakeSelector):
        def click(self, timeout=10):
            raise RemoteDisconnected("remote closed")

    class DisconnectedDevice(FakeDevice):
        def __call__(self, **_selector):
            return DisconnectedSelector()

    driver = UIDriver(FakeADB(tmp_path), device=DisconnectedDevice())

    # Step 1：模拟点击过程中 u2 HTTP 连接被设备端关闭。
    with pytest.raises(DeviceTransportError, match="during click element"):
        driver.click_text("系统")


def test_screenshots_keep_first_and_final_evidence(tmp_path):
    """验证连续截图使用不同文件，首次失败证据不会被覆盖。"""
    device = FakeDevice()
    device.screenshot = lambda: SimpleNamespace(save=lambda path: Path(path).write_bytes(b"image"))
    driver = UIDriver(FakeADB(tmp_path), device=device)

    # Step 1：模拟首次失败和最终失败分别采集截图。
    first_path = driver.take_screenshot()
    final_path = driver.take_screenshot()

    # Step 2：确认两张截图均可独立读取。
    assert first_path != final_path
    assert first_path.read_bytes() == b"image"
    assert final_path.read_bytes() == b"image"


def test_ai_ui_tools_expose_supported_driver_operations():
    """验证 AI UI 工具名称及元素查找结果结构。"""
    tools = ui_tools(SimpleNamespace(find_element=lambda *_args, **_kwargs: {"text": "Network", "clickable": True}))
    names = {tool.name for tool in tools}

    assert names == {
        "find_element",
        "exists",
        "click",
        "click_text",
        "dump_ui",
        "take_screenshot",
        "back",
        "home",
    }

    find_tool = next(tool for tool in tools if tool.name == "find_element")
    response = json.loads(find_tool.execute('{"attribute":"text","value":"Network"}'))
    assert response == {"ok": True, "result": {"text": "Network", "clickable": True}}
