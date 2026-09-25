from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from ai.tools.ui_tool import ui_tools
from devices.ui import UIDriver


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

    def __call__(self, **selector):
        """记录定位条件并返回假元素。"""
        self.selectors.append(selector)
        return FakeSelector()

    def press(self, key):
        """记录发送的按键。"""
        self.pressed.append(key)

    def dump_hierarchy(self):
        """返回一段固定的 UI hierarchy XML。"""
        return '<hierarchy><node text="Network" /></hierarchy>'


class FakeADB:
    def __init__(self, reports_dir: Path):
        """提供测试用报告目录和设备 serial。"""
        self.reports_dir = reports_dir
        self.device_serial = "192.168.1.20:5555"


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

    assert device.selectors == [{"text": "Network"}] * 4
    assert device.pressed == ["back", "home"]


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
