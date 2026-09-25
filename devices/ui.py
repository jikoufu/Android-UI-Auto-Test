"""Android UI automation backed by uiautomator2.

Saved XML files can still be inspected offline with ``find_element(xml_path=...)``.
Live UI operations always use uiautomator2; ADB remains responsible for system
commands and diagnostics.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import uiautomator2 as u2

from devices.adb import ADBClient


class UIDriverError(RuntimeError):
    """Raised when a uiautomator2 UI operation cannot be completed."""


class UIDriver:
    _SELECTOR_ATTRIBUTES = {
        "text": "text",
        "textContains": "textContains",
        "content-desc": "description",
        "contentDescription": "description",
        "description": "description",
        "resource-id": "resourceId",
        "resourceId": "resourceId",
        "class": "className",
        "className": "className",
        "package": "packageName",
        "packageName": "packageName",
    }

    def __init__(self, adb: ADBClient, device: Any | None = None) -> None:
        self.adb = adb
        self._device = device
        self.reports_dir = adb.reports_dir

    def connect(self) -> Any:
        """Connect uiautomator2 to the same serial selected by ADBClient."""
        if self._device is not None:
            return self._device
        serial = self.adb.device_serial
        try:
            self._device = u2.connect(serial)
        except Exception as exc:
            raise UIDriverError(f"Could not connect uiautomator2 to Android device ({type(exc).__name__})") from exc
        return self._device

    def _selector(self, attribute: str, value: str) -> Any:
        selector_attribute = self._SELECTOR_ATTRIBUTES.get(attribute)
        if selector_attribute is None:
            raise ValueError(f"Unsupported UI selector attribute: {attribute}")
        return self.connect()(**{selector_attribute: value})

    def dump_ui(self) -> Path:
        """Save the current uiautomator2 hierarchy as evidence and return its path."""
        try:
            hierarchy = self.connect().dump_hierarchy()
        except Exception as exc:
            raise UIDriverError(f"Could not dump Android UI hierarchy ({type(exc).__name__})") from exc
        output_dir = self.reports_dir / "ui_dump"
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "window.xml"
        path.write_text(hierarchy, encoding="utf-8")
        return path

    def take_screenshot(self) -> Path:
        """Save a screenshot from uiautomator2 and return its path."""
        output_dir = self.reports_dir / "screenshots"
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "latest.png"
        try:
            self.connect().screenshot().save(path)
        except Exception as exc:
            raise UIDriverError(f"Could not capture Android UI screenshot ({type(exc).__name__})") from exc
        return path

    @staticmethod
    def _find_in_saved_xml(attribute: str, value: str, xml_path: str | Path) -> dict[str, str] | None:
        """Read a previously saved hierarchy for offline debugging or analysis."""
        root = ET.parse(xml_path).getroot()
        normalized_attribute = {
            "contentDescription": "content-desc",
            "resourceId": "resource-id",
            "className": "class",
            "packageName": "package",
        }.get(attribute, attribute)
        for node in root.iter("node"):
            if node.attrib.get(normalized_attribute) == value:
                return dict(node.attrib)
        return None

    def exists(self, attribute: str, value: str, timeout: float = 0) -> bool:
        if timeout < 0:
            raise ValueError("timeout must be non-negative")
        try:
            return bool(self._selector(attribute, value).exists(timeout=timeout))
        except (ValueError, UIDriverError):
            raise
        except Exception as exc:
            raise UIDriverError(f"Could not check Android UI element ({type(exc).__name__})") from exc

    def find_element(
        self,
        attribute: str,
        value: str,
        xml_path: str | Path | None = None,
        timeout: float = 0,
    ) -> dict[str, Any] | None:
        """Find a live element, or inspect a saved XML hierarchy when requested."""
        if xml_path is not None:
            return self._find_in_saved_xml(attribute, value, xml_path)
        if timeout < 0:
            raise ValueError("timeout must be non-negative")
        try:
            selector = self._selector(attribute, value)
            if not selector.exists(timeout=timeout):
                return None
            info = selector.info
        except (ValueError, UIDriverError):
            raise
        except Exception as exc:
            raise UIDriverError(f"Could not find Android UI element ({type(exc).__name__})") from exc
        return dict(info) if isinstance(info, dict) else None

    def click(self, attribute: str, value: str, timeout: float = 10) -> bool:
        """Wait for and click the selected UI element."""
        if timeout < 0:
            raise ValueError("timeout must be non-negative")
        try:
            self._selector(attribute, value).click(timeout=timeout)
        except (ValueError, UIDriverError):
            raise
        except Exception as exc:
            raise UIDriverError(f"Could not click Android UI element ({type(exc).__name__})") from exc
        return True

    def click_text(self, text: str, timeout: float = 10) -> bool:
        return self.click("text", text, timeout=timeout)

    def wait(self, attribute: str, value: str, timeout: float = 10) -> bool:
        """Wait until a UI element appears, returning whether it was found."""
        if timeout < 0:
            raise ValueError("timeout must be non-negative")
        try:
            return bool(self._selector(attribute, value).wait(timeout=timeout))
        except (ValueError, UIDriverError):
            raise
        except Exception as exc:
            raise UIDriverError(f"Could not wait for Android UI element ({type(exc).__name__})") from exc

    def wait_exists(self, attribute: str, value: str, timeout: float = 10) -> bool:
        return self.wait(attribute, value, timeout=timeout)

    def press(self, key: str) -> None:
        if not key.strip():
            raise ValueError("key must not be empty")
        try:
            self.connect().press(key.strip().lower())
        except Exception as exc:
            raise UIDriverError(f"Could not press Android key ({type(exc).__name__})") from exc

    def back(self) -> None:
        self.press("back")

    def home(self) -> None:
        self.press("home")

    def tap_element(self, attribute: str, value: str, xml_path: str | Path | None = None) -> bool:
        """Backward-compatible alias; saved XML is for offline inspection only."""
        if xml_path is not None:
            raise ValueError("Saved XML is offline evidence and cannot be used for live UI clicks")
        return self.click(attribute, value)
