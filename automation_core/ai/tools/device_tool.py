"""Android TV 设备状态工具适配层。"""

from automation_core.ai.agent import AITool
from automation_core.ai.tools.ui_tool import NoArguments
from automation_core.device.device_manager import AndroidTV


def device_tools(tv: AndroidTV) -> list[AITool]:
    """为 Agent 注册读取前台 Activity 和设备状态的工具。"""
    return [
        AITool("get_current_activity", "Read the foreground Android activity.", NoArguments, lambda _args: tv.current_activity()),
        AITool("get_device_state", "Read the connected Android device state.", NoArguments, lambda _args: tv.get_device_state()),
    ]
