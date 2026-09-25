"""Device-state tool adapters for existing AndroidTV methods."""

from ai.agent import AITool
from ai.tools.ui_tool import NoArguments
from devices.tv import AndroidTV


def device_tools(tv: AndroidTV) -> list[AITool]:
    return [
        AITool("get_current_activity", "Read the foreground Android activity.", NoArguments, lambda _args: tv.current_activity()),
        AITool("get_device_state", "Read the connected Android device state.", NoArguments, lambda _args: tv.get_device_state()),
    ]
