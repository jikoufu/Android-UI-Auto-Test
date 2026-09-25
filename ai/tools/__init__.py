"""将 Android TV 现有设备能力适配为 AI Agent 工具。"""

from __future__ import annotations

from ai.agent import ToolRegistry
from ai.tools.adb_tool import adb_shell_tool
from ai.tools.device_tool import device_tools
from ai.tools.remote_tool import remote_key_tool
from ai.tools.ui_tool import ui_tools
from devices.tv import AndroidTV


def build_device_tools(tv: AndroidTV) -> ToolRegistry:
    """基于 Android TV 门面构建完整设备工具注册表。"""
    return ToolRegistry(
        [
            adb_shell_tool(tv.adb),
            remote_key_tool(tv.remote),
            *ui_tools(tv.ui),
            *device_tools(tv),
        ]
    )


__all__ = ["build_device_tools"]
