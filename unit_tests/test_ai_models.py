import pytest
from pydantic import ValidationError

from models.ai_result import AIAnalysisResult, RecoveryAction
from models.device_state import DeviceState


def test_analysis_result_validates_action_and_confidence():
    """验证合法的分析结果会解析为枚举动作。"""
    result = AIAnalysisResult(
        error_type="element_missing",
        reason="The requested label was not in the UI dump.",
        suggested_action="refind_element",
        confidence=0.8,
    )

    assert result.suggested_action is RecoveryAction.REFIND_ELEMENT


def test_analysis_result_rejects_out_of_range_confidence():
    """验证置信度超出范围时模型校验失败。"""
    with pytest.raises(ValidationError):
        AIAnalysisResult(
            error_type="unknown",
            reason="No evidence.",
            suggested_action="human_intervention",
            confidence=1.1,
        )


def test_device_state_requires_a_selected_device():
    """验证设备状态必须包含有效 serial。"""
    state = DeviceState(serial="emulator-5554", connected=True)

    assert state.serial == "emulator-5554"
