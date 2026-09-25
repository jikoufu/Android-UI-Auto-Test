import pytest

from flows.settings_flow import SettingsFlow
from models.ai_result import RecoveryAction


@pytest.mark.device
@pytest.mark.ai
def test_ai_analyzes_missing_device_name_element(ai_analyzer, failure_context_collector, tv, ui):
    """验证真实 Android 设置失败现场可交给 AI 返回结构化分析。"""
    settings = SettingsFlow(tv)

    # Step 1：打开系统设置并进入“我的设备”页面。
    settings.open_settings()
    assert settings.is_settings_page()
    assert ui.click_text("我的设备", timeout=10)

    # Step 2：捕获不存在元素的 UI 错误，并收集真实设备现场证据。
    with pytest.raises(Exception) as failure:
        ui.click_text("设备名称_AI_TEST_NOT_EXIST", timeout=2)
    context = failure_context_collector.collect(
        step_name="查找不存在的设备名称元素",
        error=failure.value,
        attempt=1,
    )

    # Step 3：调用真实 AI Analyzer，并输出结构化分析结果供测试报告查看。
    analysis = ai_analyzer.analyze(
        failure=f"{type(failure.value).__name__}: {failure.value}",
        context=context,
    )
    print(
        "AI analysis: "
        f"error_type={analysis.error_type}, "
        f"current_state={analysis.current_state}, "
        f"reason={analysis.reason}, "
        f"suggested_action={analysis.suggested_action.value}, "
        f"confidence={analysis.confidence}, "
        f"evidence={analysis.evidence}"
    )

    assert analysis.suggested_action in {
        RecoveryAction.REFIND_ELEMENT,
        RecoveryAction.RETRY,
        RecoveryAction.STOP,
        RecoveryAction.HUMAN_INTERVENTION,
    }
    assert context["ui_hierarchy"]
