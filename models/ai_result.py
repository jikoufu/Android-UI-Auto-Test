"""Structured AI analysis output."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class RecoveryAction(StrEnum):
    RETRY = "retry"
    BACK = "back"
    REENTER_PAGE = "reenter_page"
    REFIND_ELEMENT = "refind_element"
    RETRY_FLOW = "retry_flow"
    STOP = "stop"
    HUMAN_INTERVENTION = "human_intervention"


class AIAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error_type: str = Field(min_length=1)
    current_state: str | None = None
    reason: str = Field(min_length=1)
    suggested_action: RecoveryAction
    confidence: float = Field(ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)
