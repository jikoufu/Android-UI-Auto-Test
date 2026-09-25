"""遥控器工具适配层；按键映射由 devices.remote 统一管理。"""

from pydantic import BaseModel, ConfigDict, Field

from ai.agent import AITool
from devices.remote import RemoteController


class RemoteKeyArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=32)


def remote_key_tool(remote: RemoteController) -> AITool:
    """把遥控器按键能力封装为 Agent 工具。"""
    return AITool("press_remote_key", "Press one supported Android TV remote key.", RemoteKeyArguments, lambda args: remote.press_key(args.key))
