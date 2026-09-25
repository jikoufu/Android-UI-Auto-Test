"""ADB Shell 工具适配层；命令执行仍由 devices.adb 负责。"""

from pydantic import BaseModel, ConfigDict, Field

from ai.agent import AITool
from devices.adb import ADBClient


class ShellArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str = Field(min_length=1, max_length=500)


def adb_shell_tool(adb: ADBClient) -> AITool:
    """把 ADB shell 命令封装为 Agent 可调用工具。"""
    return AITool("adb_shell", "Run a command on the selected Android device.", ShellArguments, lambda args: adb.run_shell(args.command))
