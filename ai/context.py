"""收集一次测试步骤失败时可用的 Android 设备证据。"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from devices.tv import AndroidTV
from devices.ui import UIDriver


class FailureContextCollector:
    def __init__(self, tv: AndroidTV, ui: UIDriver, max_ui_chars: int = 0) -> None:
        """保存设备对象；默认只发送结构化节点，原始 XML 留在证据文件。"""
        if max_ui_chars < 0:
            raise ValueError("max_ui_chars must not be negative")
        self.tv = tv
        self.ui = ui
        self.max_ui_chars = max_ui_chars

    def collect(
        self,
        *,
        step_name: str,
        error: Exception,
        attempt: int,
        previous_errors: list[str] | None = None,
    ) -> dict[str, Any]:
        """独立采集各项设备证据，单项失败时保留错误并继续。"""
        context: dict[str, Any] = {
            "step_name": step_name,
            "exception_type": type(error).__name__,
            "exception_message": str(error)[:2000],
            "attempt": attempt,
            "previous_errors": list(previous_errors or []),
            "current_activity": None,
            "device_state": None,
            "ui_dump_path": None,
            "ui_hierarchy": None,
            "ui_hierarchy_truncated": False,
            "ui_texts": [],
            "ui_texts_truncated": False,
            "ui_elements": [],
            "ui_elements_truncated": False,
            "scrollable_nodes": [],
            "screenshot_path": None,
        }

        try:
            context["current_activity"] = self.tv.current_activity()
        except Exception as exc:
            context["current_activity_error"] = self._error_summary(exc)

        try:
            state = self.tv.get_device_state()
            context["device_state"] = state.model_dump(mode="json") if hasattr(state, "model_dump") else state
        except Exception as exc:
            context["device_state_error"] = self._error_summary(exc)

        try:
            dump_path = Path(self.ui.dump_ui())
            context["ui_dump_path"] = str(dump_path)
            try:
                hierarchy = dump_path.read_text(encoding="utf-8")
                if self.max_ui_chars:
                    context["ui_hierarchy"] = hierarchy[: self.max_ui_chars]
                    context["ui_hierarchy_truncated"] = len(hierarchy) > self.max_ui_chars
                elements, truncated = self._visible_elements(hierarchy)
                context["ui_elements"] = elements
                context["ui_elements_truncated"] = truncated
                context["ui_texts"] = list(dict.fromkeys(
                    value for element in elements
                    for value in (element.get("text"), element.get("content_desc")) if value
                ))
                context["ui_texts_truncated"] = truncated
                context["scrollable_nodes"] = [
                    element for element in elements if element.get("scrollable")
                ]
            except Exception as exc:
                context["ui_hierarchy_error"] = self._error_summary(exc)
        except Exception as exc:
            context["ui_dump_error"] = self._error_summary(exc)

        try:
            context["screenshot_path"] = str(self.ui.take_screenshot())
        except Exception as exc:
            context["screenshot_error"] = self._error_summary(exc)

        return context

    @staticmethod
    def _error_summary(error: Exception) -> str:
        """记录精简异常信息，避免堆栈和大段设备输出进入上下文。"""
        return f"{type(error).__name__}: {str(error)[:300]}"

    @staticmethod
    def _visible_elements(hierarchy: str) -> tuple[list[dict[str, Any]], bool]:
        """提取有限数量的可见交互节点和标签，供模型判断与点击校验。"""
        root = ET.fromstring(hierarchy)
        elements: list[dict[str, Any]] = []
        for node in root.iter("node"):
            if node.attrib.get("visible-to-user", "true").lower() != "true":
                continue
            attrs = node.attrib
            text = attrs.get("text", "").strip()[:120]
            description = attrs.get("content-desc", "").strip()[:120]
            clickable = attrs.get("clickable") == "true"
            scrollable = attrs.get("scrollable") == "true"
            if not (text or description or clickable or scrollable):
                continue
            if len(elements) >= 80:
                return elements, True
            elements.append({
                "text": text,
                "content_desc": description,
                "resource_id": attrs.get("resource-id", "")[:120],
                "class": attrs.get("class", "")[:100],
                "clickable": clickable,
                "scrollable": scrollable,
                "bounds": attrs.get("bounds", ""),
                "focused": attrs.get("focused") == "true",
            })
        return elements, False
