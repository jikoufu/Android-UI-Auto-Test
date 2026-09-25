"""Remote-control tool adapter; key mapping stays in devices.remote."""

from pydantic import BaseModel, ConfigDict, Field

from ai.agent import AITool
from devices.remote import RemoteController


class RemoteKeyArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=32)


def remote_key_tool(remote: RemoteController) -> AITool:
    return AITool("press_remote_key", "Press one supported Android TV remote key.", RemoteKeyArguments, lambda args: remote.press_key(args.key))
