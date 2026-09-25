from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from ai.tools.ui_tool import ui_tools
from devices.ui import UIDriver


class FakeSelector:
    info = {"text": "Network", "className": "android.widget.TextView"}

    def exists(self, timeout=0):
        return timeout <= 2

    def wait(self, timeout=10):
        return timeout <= 2

    def click(self, timeout=10):
        return None


class FakeDevice:
    def __init__(self):
        self.selectors = []
        self.pressed = []

    def __call__(self, **selector):
        self.selectors.append(selector)
        return FakeSelector()

    def press(self, key):
        self.pressed.append(key)

    def dump_hierarchy(self):
        return '<hierarchy><node text="Network" /></hierarchy>'


class FakeADB:
    def __init__(self, reports_dir: Path):
        self.reports_dir = reports_dir
        self.device_serial = "192.168.1.20:5555"


def test_connect_uses_adb_selected_serial(tmp_path, monkeypatch):
    device = FakeDevice()
    connected_serials = []
    fake_module = SimpleNamespace(connect=lambda serial: (connected_serials.append(serial), device)[1])
    monkeypatch.setattr("devices.ui.u2", fake_module)

    driver = UIDriver(FakeADB(tmp_path))

    assert driver.connect() is device
    assert connected_serials == ["192.168.1.20:5555"]


def test_live_ui_actions_use_uiautomator_selectors(tmp_path):
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
