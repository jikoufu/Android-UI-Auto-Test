"""将 Android TV 现有设备能力适配为 AI Agent 工具。"""

from __future__ import annotations

from ai.agent import ToolRegistry
from ai.tools.device_tool import device_tools
from ai.tools.remote_tool import remote_key_tool
from ai.tools.ui_tool import ui_tools
from devices.tv import AndroidTV


def build_device_tools(tv: AndroidTV) -> ToolRegistry:
    """构建默认白名单设备工具，不向 Agent 暴露任意 adb shell。"""
    allowed_names = {
        "get_current_activity",
        "get_device_state",
        "find_element",
        "exists",
        "dump_ui",
        "back",
        "home",
        "click",
        "click_text",
        "press_remote_key",
    }
    tools = [remote_key_tool(tv.remote), *ui_tools(tv.ui), *device_tools(tv)]
    return ToolRegistry(
        [tool for tool in tools if tool.name in allowed_names]
    )


__all__ = ["build_device_tools"]
