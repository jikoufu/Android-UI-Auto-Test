"""UI helper tool adapters; all UI work is delegated to devices.ui."""

from pydantic import BaseModel, ConfigDict, Field

from ai.agent import AITool
from devices.ui import UIDriver


class NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FindElementArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attribute: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=300)


def ui_tools(ui: UIDriver) -> list[AITool]:
    return [
        AITool("dump_ui", "Save the current UIAutomator XML dump and return its path.", NoArguments, lambda _args: str(ui.dump_ui())),
        AITool("take_screenshot", "Save a screenshot and return its path.", NoArguments, lambda _args: str(ui.take_screenshot())),
        AITool("find_ui_element", "Find a UIAutomator node by one XML attribute.", FindElementArguments, lambda args: ui.find_element(args.attribute, args.value)),
    ]
