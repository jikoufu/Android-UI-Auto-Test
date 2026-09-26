"""收集一次测试步骤失败时可用的 Android 设备证据。"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from time import perf_counter
from typing import Any

from devices.tv import AndroidTV
from devices.ui import DeviceTransportError, UIDriver


class FailureContextCollector:
    def __init__(self, tv: AndroidTV, ui: UIDriver, max_ui_chars: int = 0) -> None:
        """保存设备对象；默认只发送结构化节点，原始 XML 留在证据文件。"""
        if max_ui_chars < 0:
            raise ValueError("max_ui_chars must not be negative")
        self.tv = tv
        self.ui = ui
        self.max_ui_chars = max_ui_chars
        self._cached_static_device_state: Any | None = None

    def collect(
        self,
        *,
        step_name: str,
        error: Exception,
        attempt: int,
        previous_errors: list[str] | None = None,
        include_screenshot: bool = True,
        refresh_device_state: bool = False,
        include_device_state: bool = True,
        include_activity: bool = True,
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
            "ui_dump_backend": None,
            "transport_recovery": None,
            "transport_unavailable": False,
            "ui_hierarchy": None,
            "ui_hierarchy_truncated": False,
            "ui_texts": [],
            "ui_texts_truncated": False,
            "ui_elements": [],
            "ui_elements_truncated": False,
            "scrollable_nodes": [],
            "screenshot_path": None,
            "context_collection_duration_ms": 0.0,
            "ui_dump_duration_ms": 0.0,
        }
        if refresh_device_state:
            self._cached_static_device_state = None

        collection_started = perf_counter()
        dump_started = perf_counter()
        try:
            dump_path = Path(self.ui.dump_ui())
            context["ui_dump_path"] = str(dump_path)
            context["ui_dump_backend"] = getattr(self.ui, "last_dump_backend", None)
            context["transport_recovery"] = getattr(self.ui, "last_transport_recovery", None)
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
                context["scrollable_nodes"] = [element for element in elements if element.get("scrollable")]
                context["available_navigation_candidates"] = self._navigation_candidates(elements)
            except Exception as exc:
                context["ui_hierarchy_error"] = self._error_summary(exc)
        except Exception as exc:
            context["ui_dump_error"] = self._error_summary(exc)
            context["ui_dump_backend"] = getattr(self.ui, "last_dump_backend", None)
            context["transport_recovery"] = getattr(self.ui, "last_transport_recovery", None)
            context["transport_unavailable"] = isinstance(exc, DeviceTransportError)
        context["ui_dump_duration_ms"] = round((perf_counter() - dump_started) * 1000, 2)

        if include_activity:
            try:
                try:
                    context["current_activity"] = self.tv.current_activity(timeout=2)
                except TypeError:
                    context["current_activity"] = self.tv.current_activity()
            except Exception as exc:
                context["current_activity_error"] = self._error_summary(exc)

        if include_device_state and self._cached_static_device_state is None:
            try:
                getter = getattr(self.tv, "get_static_device_state", None)
                if getter is not None:
                    state = getter(timeout=3)
                else:
                    try:
                        state = self.tv.get_device_state(include_activity=False)
                    except TypeError:
                        state = self.tv.get_device_state()
                cached = state.model_dump(mode="json") if hasattr(state, "model_dump") else dict(state)
                cached.pop("current_activity", None)
                self._cached_static_device_state = cached
            except Exception as exc:
                context["device_state_error"] = self._error_summary(exc)
        context["device_state"] = self._cached_static_device_state

        if include_screenshot:
            try:
                context["screenshot_path"] = str(self.ui.take_screenshot())
            except Exception as exc:
                context["screenshot_error"] = self._error_summary(exc)

        context["context_collection_duration_ms"] = round((perf_counter() - collection_started) * 1000, 2)
        return context

    @staticmethod
    def _navigation_candidates(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """从可见 hierarchy 过滤安全导航入口，并生成当前快照内的 ID。"""
        candidates = []
        for element in elements:
            if not element.get("clickable") or element.get("checkable") or element.get("editable"):
                continue
            if not element.get("enabled", True):
                continue
            label = element.get("text") or element.get("content_desc")
            if not label:
                continue
            class_name = element.get("class", "")
            if class_name.endswith("EditText"):
                role = "input"
            elif class_name.endswith("Button"):
                role = "button"
            elif "TextView" in class_name or "Layout" in class_name or not class_name:
                role = "navigation"
            else:
                role = "unknown"
            if role != "navigation":
                continue
            resource_id = element.get("resource_id", "")
            description = element.get("content_desc", "")
            stable_key = (f"id:{resource_id}" if resource_id else
                          f"desc:{description}|class:{class_name}" if description else
                          f"text:{label}|class:{class_name}")
            candidates.append({
                "candidate_id": f"e{len(candidates) + 1}", "label": label,
                "resource_id": resource_id, "content_desc": description,
                "text": element.get("text", ""), "class_name": class_name,
                "role": role, "stable_key": stable_key,
            })
        return candidates

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
            checkable = attrs.get("checkable") == "true"
            enabled = attrs.get("enabled", "true") == "true"
            editable = attrs.get("editable") == "true"
            if not (text or description or clickable or scrollable):
                continue
            if len(elements) >= 80:
                return elements, True
            elements.append({
                "text": text,
                "content_desc": description,
                "resource_id": attrs.get("resource-id", "")[:120],
                "class": attrs.get("class", "")[:100],
                "package": attrs.get("package", "")[:120],
                "clickable": clickable,
                "scrollable": scrollable,
                "checkable": checkable,
                "checked": attrs.get("checked") == "true",
                "enabled": enabled,
                "selected": attrs.get("selected") == "true",
                "focusable": attrs.get("focusable") == "true",
                "editable": editable,
                "bounds": attrs.get("bounds", ""),
                "focused": attrs.get("focused") == "true",
            })
        return elements, False
