"""Agent 运行结束后返回的状态和摘要。"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class AgentStatus(StrEnum):
    """Agent 单次运行的结束状态。"""
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


class AgentRunResult(BaseModel):
    """Agent 运行状态、消息和工具调用次数。"""
    model_config = ConfigDict(extra="forbid")

    status: AgentStatus
    message: str
    tool_calls: int = Field(ge=0)
