from ai.recovery import RecoveryManager
from models.ai_result import RecoveryAction
from models.recovery_result import RecoveryStatus


def test_recovery_stops_after_configured_max_steps():
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
    result = RecoveryManager(max_steps=3).recover(
        decide_action=lambda _step, _errors: RecoveryAction.RETRY,
        handlers={RecoveryAction.RETRY: lambda: None},
        verify=lambda: False,
    )

    assert result.status is RecoveryStatus.NEEDS_HUMAN
    assert result.attempts == 1
    assert "Repeated" in result.reason


def test_recovery_can_stop_for_human_intervention():
    result = RecoveryManager().recover(
        decide_action=lambda _step, _errors: RecoveryAction.HUMAN_INTERVENTION,
        handlers={},
        verify=lambda: False,
    )

    assert result.status is RecoveryStatus.NEEDS_HUMAN
    assert result.attempts == 0
