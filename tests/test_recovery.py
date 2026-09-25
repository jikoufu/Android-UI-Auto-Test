from ai.recovery import RecoveryManager
from models.ai_result import RecoveryAction
from models.recovery_result import RecoveryStatus


def test_recovery_stops_after_configured_max_steps():
    """验证自动恢复在配置步数上限后停止并请求人工处理。"""
    calls: list[RecoveryAction] = []
    actions = [RecoveryAction.RETRY, RecoveryAction.BACK, RecoveryAction.RETRY_FLOW]

    result = RecoveryManager(max_steps=3).recover(
        decide_action=lambda step, _errors: actions[step],
        handlers={action: lambda action=action: calls.append(action) for action in actions},
        verify=lambda: False,
    )

    assert result.status is RecoveryStatus.NEEDS_HUMAN
    assert result.attempts == 3
    assert len(calls) == 3
    assert "limit reached" in result.reason


def test_recovery_breaks_on_repeated_action():
    """验证连续重复的恢复动作会触发熔断。"""
    result = RecoveryManager(max_steps=3).recover(
        decide_action=lambda _step, _errors: RecoveryAction.RETRY,
        handlers={RecoveryAction.RETRY: lambda: None},
        verify=lambda: False,
    )

    assert result.status is RecoveryStatus.NEEDS_HUMAN
    assert result.attempts == 1
    assert "Repeated" in result.reason


def test_recovery_can_stop_for_human_intervention():
    """验证 AI 请求人工介入时不会执行恢复动作。"""
    result = RecoveryManager().recover(
        decide_action=lambda _step, _errors: RecoveryAction.HUMAN_INTERVENTION,
        handlers={},
        verify=lambda: False,
    )

    assert result.status is RecoveryStatus.NEEDS_HUMAN
    assert result.attempts == 0
