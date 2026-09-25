"""基于 uiautomator2 的 Android UI 驱动。

实时 UI 操作统一通过 uiautomator2 完成；已保存的 XML 仅用于离线调试和分析。
ADB 仍负责系统命令、设备状态和诊断。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import uiautomator2 as u2

from devices.adb import ADBClient


class UIDriverError(RuntimeError):
    """uiautomator2 UI 操作失败时抛出的异常。"""


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
        """保存共享的 ADB 客户端；设备连接延迟到首次 UI 操作时建立。"""
        self.adb = adb
        self._device = device
        self.reports_dir = adb.reports_dir

    def connect(self) -> Any:
        """连接 ADB 已选定的设备 serial，并复用连接结果。"""
        if self._device is not None:
            return self._device
        # ADB 与 uiautomator2 使用同一个 serial，避免两套设备选择逻辑不一致。
        serial = self.adb.device_serial
        try:
            self._device = u2.connect(serial)
        except Exception as exc:
            raise UIDriverError(f"Could not connect uiautomator2 to Android device ({type(exc).__name__})") from exc
        return self._device

    def _selector(self, attribute: str, value: str) -> Any:
        """将 XML 属性名转换为 uiautomator2 Selector 支持的参数。"""
        selector_attribute = self._SELECTOR_ATTRIBUTES.get(attribute)
        if selector_attribute is None:
            raise ValueError(f"Unsupported UI selector attribute: {attribute}")
        return self.connect()(**{selector_attribute: value})

    def dump_ui(self) -> Path:
        """保存当前界面的 XML hierarchy，返回证据文件路径。"""
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
        """保存当前设备截图，返回图片文件路径。"""
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
        """只解析已有 XML 文件，用于离线调试；此方法不会操作设备。"""
        root = ET.parse(xml_path).getroot()
        # uiautomator2 的属性名与旧 XML dump 的属性名存在差异，在此处统一。
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
        """检查元素是否存在；timeout 为等待秒数，默认立即返回。"""
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
        """查找实时界面元素；传入 xml_path 时改为读取保存的 XML。"""
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
        """等待指定元素出现并点击，成功后返回 True。"""
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
        """按界面上的完整文字查找并点击元素。"""
        return self.click("text", text, timeout=timeout)

    def wait(self, attribute: str, value: str, timeout: float = 10) -> bool:
        """等待指定元素出现；超时后返回 False。"""
        if timeout < 0:
            raise ValueError("timeout must be non-negative")
        try:
            return bool(self._selector(attribute, value).wait(timeout=timeout))
        except (ValueError, UIDriverError):
            raise
        except Exception as exc:
            raise UIDriverError(f"Could not wait for Android UI element ({type(exc).__name__})") from exc

    def wait_exists(self, attribute: str, value: str, timeout: float = 10) -> bool:
        """wait 的兼容别名，等待元素出现并返回是否找到。"""
        return self.wait(attribute, value, timeout=timeout)

    def press(self, key: str) -> None:
        """通过 uiautomator2 发送 Android 按键名称，例如 back 或 home。"""
        if not key.strip():
            raise ValueError("key must not be empty")
        try:
            self.connect().press(key.strip().lower())
        except Exception as exc:
            raise UIDriverError(f"Could not press Android key ({type(exc).__name__})") from exc

    def back(self) -> None:
        """发送 Android 返回键。"""
        self.press("back")

    def scroll(self, direction: str) -> None:
        """在当前界面纵向滚动一屏。"""
        if direction not in {"up", "down"}:
            raise ValueError("direction must be 'up' or 'down'")
        try:
            # 手指向上滑动时列表内容向下，反之亦然。
            gesture = "up" if direction == "down" else "down"
            self.connect().swipe_ext(gesture, scale=0.7)
        except Exception as exc:
            raise UIDriverError(f"Could not scroll Android UI ({type(exc).__name__})") from exc

    def home(self) -> None:
        """发送 Android 主屏键。"""
        self.press("home")

    def tap_element(self, attribute: str, value: str, xml_path: str | Path | None = None) -> bool:
        """兼容旧调用的点击别名；保存的 XML 只能离线检查，不能用于实时点击。"""
        if xml_path is not None:
            raise ValueError("Saved XML is offline evidence and cannot be used for live UI clicks")
        return self.click(attribute, value)
