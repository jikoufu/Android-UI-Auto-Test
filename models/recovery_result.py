"""Outcome of a bounded recovery attempt."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from models.ai_result import RecoveryAction


class RecoveryStatus(StrEnum):
    COMPLETED = "completed"
    STOPPED = "stopped"
    NEEDS_HUMAN = "needs_human"
    FAILED = "failed"


class RecoveryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: RecoveryStatus
    attempts: int = Field(ge=0)
    last_action: RecoveryAction | None = None
    reason: str
    errors: list[str] = Field(default_factory=list)
