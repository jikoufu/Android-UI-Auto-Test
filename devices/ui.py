"""Small UIAutomator XML reader and coordinate tap helper."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from devices.adb import ADBClient


class UIDriver:
    _BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")

    def __init__(self, adb: ADBClient) -> None:
        self.adb = adb

    def dump_ui(self) -> Path:
        return self.adb.dump_ui()

    def take_screenshot(self) -> Path:
        return self.adb.take_screenshot()

    def find_element(self, attribute: str, value: str, xml_path: str | Path | None = None) -> dict[str, str] | None:
        path = Path(xml_path) if xml_path else self.dump_ui()
        root = ET.parse(path).getroot()
        for node in root.iter("node"):
            if node.attrib.get(attribute) == value:
                return dict(node.attrib)
        return None

    def tap_element(self, attribute: str, value: str, xml_path: str | Path | None = None) -> bool:
        element = self.find_element(attribute, value, xml_path)
        if not element:
            return False
        match = self._BOUNDS.fullmatch(element.get("bounds", ""))
        if not match:
            return False
        left, top, right, bottom = (int(part) for part in match.groups())
        x, y = (left + right) // 2, (top + bottom) // 2
        self.adb.run_shell(f"input tap {x} {y}")
        return True
