"""Agent execution status returned to callers."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class AgentStatus(StrEnum):
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


class AgentRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: AgentStatus
    message: str
    tool_calls: int = Field(ge=0)
