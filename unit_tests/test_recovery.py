import pytest

from ai.recovery import RecoveryManager
from devices.ui import DeviceTransportError
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


def test_fatal_device_transport_error_is_not_sent_to_next_ai_decision():
    """验证恢复动作的设备通信故障直接中止业务恢复循环。"""
    decisions = 0

    def decide(_step, _errors):
        nonlocal decisions
        decisions += 1
        return RecoveryAction.BACK

    def disconnected():
        raise DeviceTransportError("Device UI transport unavailable")

    # Step 1：让第一次恢复动作遭遇设备通信故障。
    with pytest.raises(DeviceTransportError):
        RecoveryManager(max_steps=3).recover(
            decide_action=decide,
            handlers={RecoveryAction.BACK: disconnected},
            verify=lambda: False,
            fatal_exceptions=(DeviceTransportError,),
        )

    # Step 2：确认没有进入下一轮 AI 决策。
    assert decisions == 1
