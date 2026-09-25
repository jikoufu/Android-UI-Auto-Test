"""Adapters that expose existing AndroidTV device methods to the AI agent."""

from __future__ import annotations

from ai.agent import ToolRegistry
from ai.tools.adb_tool import adb_shell_tool
from ai.tools.device_tool import device_tools
from ai.tools.remote_tool import remote_key_tool
from ai.tools.ui_tool import ui_tools
from devices.tv import AndroidTV


def build_device_tools(tv: AndroidTV) -> ToolRegistry:
    """Build tools by adapting the existing AndroidTV device facade."""
    return ToolRegistry(
        [
            adb_shell_tool(tv.adb),
            remote_key_tool(tv.remote),
            *ui_tools(tv.ui),
            *device_tools(tv),
        ]
    )


__all__ = ["build_device_tools"]
