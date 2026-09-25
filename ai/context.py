"""收集一次测试步骤失败时可用的 Android 设备证据。"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from devices.tv import AndroidTV
from devices.ui import UIDriver


class FailureContextCollector:
    def __init__(self, tv: AndroidTV, ui: UIDriver, max_ui_chars: int = 40_000) -> None:
        """保存共享设备对象，并限制发送给模型的 hierarchy 长度。"""
        if max_ui_chars < 1:
            raise ValueError("max_ui_chars must be at least 1")
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
                context["ui_hierarchy"] = hierarchy[: self.max_ui_chars]
                context["ui_hierarchy_truncated"] = len(hierarchy) > self.max_ui_chars
                context["ui_texts"] = self._visible_ui_texts(hierarchy)
                context["ui_texts_truncated"] = len(context["ui_texts"]) >= 200
                context["scrollable_nodes"] = self._scrollable_nodes(hierarchy)
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
    def _visible_ui_texts(hierarchy: str) -> list[str]:
        """从当前 UI XML 提取可见节点的文字和描述，供导航候选校验。"""
        root = ET.fromstring(hierarchy)
        texts: list[str] = []
        seen: set[str] = set()
        for node in root.iter("node"):
            if node.attrib.get("visible-to-user", "true").lower() != "true":
                continue
            for attribute in ("text", "content-desc"):
                value = node.attrib.get(attribute, "").strip()
                if value and value not in seen:
                    seen.add(value)
                    texts.append(value[:200])
                    if len(texts) >= 200:
                        return texts
        return texts

    @staticmethod
    def _scrollable_nodes(hierarchy: str) -> list[dict[str, str]]:
        """提取可滚动容器的定位信息，帮助 AI 判断是否还有未查看内容。"""
        root = ET.fromstring(hierarchy)
        nodes: list[dict[str, str]] = []
        for node in root.iter("node"):
            if node.attrib.get("scrollable", "false").lower() != "true":
                continue
            nodes.append(
                {
                    key: node.attrib.get(key, "")
                    for key in ("resource-id", "class", "package", "bounds")
                }
            )
            if len(nodes) >= 40:
                break
        return nodes
