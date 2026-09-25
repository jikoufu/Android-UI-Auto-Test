import pytest
from pydantic import ValidationError

from models.ai_result import AIAnalysisResult, RecoveryAction
from models.device_state import DeviceState


def test_analysis_result_validates_action_and_confidence():
    result = AIAnalysisResult(
        error_type="element_missing",
        reason="The requested label was not in the UI dump.",
        suggested_action="refind_element",
        confidence=0.8,
    )

    assert result.suggested_action is RecoveryAction.REFIND_ELEMENT


def test_analysis_result_rejects_out_of_range_confidence():
    with pytest.raises(ValidationError):
        AIAnalysisResult(
            error_type="unknown",
            reason="No evidence.",
            suggested_action="human_intervention",
            confidence=1.1,
        )


def test_device_state_requires_a_selected_device():
    state = DeviceState(serial="emulator-5554", connected=True)

    assert state.serial == "emulator-5554"
