import pytest

from ai.executor import AIRecoveryError
from flows.settings_flow import SettingsFlow


@pytest.mark.device
@pytest.mark.ai
def test_ai_navigates_from_my_device_to_developer_options(ai_executor, tv, ui):
    """验证 AI 能从错误的“我的设备”页面返回设置并找到开发者选项。"""
    settings = SettingsFlow(tv)

    # Step 1：打开系统设置并进入“我的设备”页面。
    settings.open_settings()
    assert settings.is_settings_page()
    assert ui.click_text("我的设备", timeout=10)
    assert ui.exists("text", "设备名称", timeout=10)

    # Step 2：只提供目标路径，由 AI 根据当前页面决定返回、滚动和点击入口。
    try:
        ai_executor.run_step(
            name="test_ai_navigates_from_my_device_to_developer_options",
            goal="进入开发者选项页面",
            action=lambda: ui.click_text("开发者选项", timeout=3),
            back_retries_action=False,
            max_recovery_steps=10,
            verify=lambda: any(
                ui.exists("text", label, timeout=2)
                for label in ("开启开发者选项", "USB调试", "USB 调试", "USB debugging")
            ),
        )
    except AIRecoveryError as error:
        print(
            "导航诊断："
            f"原因={error.reason}；"
            f"动作历史={(error.context or {}).get('previous_actions')}；"
            f"当前页面={error.analysis.current_state if error.analysis else None}；"
            f"建议={error.analysis.suggested_action.value if error.analysis else None}；"
            f"目标={error.analysis.target_text if error.analysis else None}；"
            f"置信度={error.analysis.confidence if error.analysis else None}；"
            f"路径日志={ai_executor.trace_path}；"
            f"可见文字={(error.context or {}).get('ui_texts')}"
        )
        raise

    # Step 3：确认最终已进入开发者选项页面。
    assert any(
        ui.exists("text", label, timeout=2)
        for label in ("开发者选项", "开启开发者选项")
    )
    print(f"AI 路径日志：{ai_executor.trace_path}")
