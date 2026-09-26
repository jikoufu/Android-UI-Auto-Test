"""AI 失败分析和恢复建议的数据模型。"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class RecoveryAction(StrEnum):
    """AI 可建议执行的恢复动作。"""
    RETRY = "retry"
    BACK = "back"
    NAVIGATE = "navigate"
    SCROLL = "scroll"
    REENTER_PAGE = "reenter_page"
    REFIND_ELEMENT = "refind_element"
    RETRY_FLOW = "retry_flow"
    STOP = "stop"
    HUMAN_INTERVENTION = "human_intervention"


class AIAnalysisResult(BaseModel):
    """经过字段校验的 AI 分析结果。"""
    model_config = ConfigDict(extra="forbid")

    error_type: str = Field(min_length=1)
    current_state: str | None = None
    reason: str = Field(min_length=1)
    suggested_action: RecoveryAction
    confidence: float = Field(ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)
    decision_steps: list[str] = Field(default_factory=list)
    reason_note: str | None = None
    target_text: str | None = Field(default=None, min_length=1, max_length=100)
    target_candidate_id: str | None = Field(default=None, min_length=1, max_length=20)
    scroll_direction: str | None = Field(default=None, pattern="^(up|down)$")
