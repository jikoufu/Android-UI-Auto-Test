"""使用稳定应用节点识别页面，不依赖 AI 或 Web 服务。"""

from hashlib import sha256
import json
from typing import Any


def application_elements(context: dict[str, Any]) -> list[dict[str, Any]]:
    """排除已知系统状态栏，其它厂商可见节点默认保留。"""
    return [
        element for element in context.get("ui_elements", [])
        if element.get("package") != "com.android.systemui"
    ]


def build_state_fingerprint(context: dict[str, Any]) -> str:
    """用稳定的应用页面字段生成导航及动作熔断共用的指纹。"""
    elements = [
        {key: element.get(key) for key in ("text", "content_desc", "resource_id", "clickable", "scrollable")}
        for element in application_elements(context)
    ]
    elements.sort(key=lambda element: json.dumps(element, ensure_ascii=False, sort_keys=True))
    state = {"current_activity": context.get("current_activity") or "", "elements": elements}
    if not state["current_activity"] and not elements:
        return "unknown"
    return sha256(json.dumps(state, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
