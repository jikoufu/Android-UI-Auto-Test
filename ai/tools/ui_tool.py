"""Structured AI tool adapters; all UI work is delegated to ``UIDriver``."""

from pydantic import BaseModel, ConfigDict, Field

from ai.agent import AITool
from devices.ui import UIDriver


class NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ElementArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attribute: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=300)
    timeout: float = Field(default=10, ge=0, le=60)


class TextArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=300)
    timeout: float = Field(default=10, ge=0, le=60)


def ui_tools(ui: UIDriver) -> list[AITool]:
    """把 UIDriver 的 UI 能力注册为结构化 AI 工具，不直接操作设备。"""
    return [
        AITool(
            "find_element",
            "Find a visible Android UI element by text, description, resource id, class, or package.",
            ElementArguments,
            lambda args: ui.find_element(args.attribute, args.value, timeout=args.timeout),
        ),
        AITool(
            "exists",
            "Check whether an Android UI element exists, waiting up to timeout seconds.",
            ElementArguments,
            lambda args: ui.exists(args.attribute, args.value, timeout=args.timeout),
        ),
        AITool(
            "click",
            "Wait for and click an Android UI element.",
            ElementArguments,
            lambda args: ui.click(args.attribute, args.value, timeout=args.timeout),
        ),
        AITool(
            "click_text",
            "Wait for and click a visible element by its exact text.",
            TextArguments,
            lambda args: ui.click_text(args.text, timeout=args.timeout),
        ),
        AITool("dump_ui", "Save the current UI hierarchy as XML and return its path.", NoArguments, lambda _args: str(ui.dump_ui())),
        AITool("take_screenshot", "Save a screenshot and return its path.", NoArguments, lambda _args: str(ui.take_screenshot())),
        AITool("back", "Press the Android Back key.", NoArguments, lambda _args: ui.back()),
        AITool("home", "Press the Android Home key.", NoArguments, lambda _args: ui.home()),
    ]
