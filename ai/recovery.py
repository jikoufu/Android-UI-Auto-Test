"""提供有步数上限和重复动作保护的恢复流程。"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from models.ai_result import RecoveryAction
from models.recovery_result import RecoveryResult, RecoveryStatus
from devices.ui import UnknownActionOutcome


class RecoveryManager:
    def __init__(self, max_steps: int = 3) -> None:
        """设置自动恢复的最大步数。"""
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        self.max_steps = max_steps

    def recover(
        self,
        decide_action: Callable[[int, list[str]], RecoveryAction],
        handlers: Mapping[RecoveryAction, Callable[[], None]],
        verify: Callable[[], bool],
        action_identity: Callable[[RecoveryAction], str] | None = None,
        max_steps: int | None = None,
        fatal_exceptions: tuple[type[Exception], ...] = (),
    ) -> RecoveryResult:
        """逐步执行恢复动作，成功、熔断或达到上限时停止。"""
        step_limit = self.max_steps if max_steps is None else max_steps
        if step_limit < 1:
            raise ValueError("max_steps must be at least 1")
        errors: list[str] = []
        previous_action: RecoveryAction | None = None
        seen_identities: set[str] = set()
        attempts = 0
        for step in range(step_limit):
            action = decide_action(step, list(errors))
            if action is RecoveryAction.STOP:
                return RecoveryResult(status=RecoveryStatus.STOPPED, attempts=attempts, last_action=action, reason="AI requested a stop", errors=errors)
            if action is RecoveryAction.HUMAN_INTERVENTION:
                return RecoveryResult(status=RecoveryStatus.NEEDS_HUMAN, attempts=attempts, last_action=action, reason="AI requested human intervention", errors=errors)
            identity = action_identity(action) if action_identity is not None else action.value
            if identity in seen_identities:
                return RecoveryResult(status=RecoveryStatus.NEEDS_HUMAN, attempts=attempts, last_action=action, reason="Repeated recovery action stopped by the safety limit", errors=errors)
            handler = handlers.get(action)
            if handler is None:
                return RecoveryResult(status=RecoveryStatus.NEEDS_HUMAN, attempts=attempts, last_action=action, reason=f"No handler is registered for {action.value}", errors=errors)
            attempts += 1
            try:
                handler()
                if verify():
                    return RecoveryResult(status=RecoveryStatus.COMPLETED, attempts=attempts, last_action=action, reason="Recovery succeeded", errors=errors)
            except UnknownActionOutcome as exc:
                # 写请求断线时不重发；继续采集现场并让下一轮基于新页面决策。
                errors.append(f"{action.value}: {type(exc).__name__}: {str(exc)[:300]}")
            except fatal_exceptions:
                raise
            except Exception as exc:
                errors.append(f"{action.value}: {type(exc).__name__}: {str(exc)[:300]}")
            previous_action = action
            seen_identities.add(identity)
        return RecoveryResult(
            status=RecoveryStatus.NEEDS_HUMAN,
            attempts=attempts,
            last_action=previous_action,
            reason=f"Recovery limit reached ({step_limit}); automatic attempts stopped",
            errors=errors,
        )
