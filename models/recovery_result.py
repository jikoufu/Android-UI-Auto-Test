"""有步数上限的自动恢复执行结果。"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from models.ai_result import RecoveryAction


class RecoveryStatus(StrEnum):
    """自动恢复流程的结束状态。"""
    COMPLETED = "completed"
    STOPPED = "stopped"
    NEEDS_HUMAN = "needs_human"
    FAILED = "failed"


class RecoveryResult(BaseModel):
    """恢复状态、尝试次数、最后动作和错误记录。"""
    model_config = ConfigDict(extra="forbid")

    status: RecoveryStatus
    attempts: int = Field(ge=0)
    last_action: RecoveryAction | None = None
    reason: str
    errors: list[str] = Field(default_factory=list)
