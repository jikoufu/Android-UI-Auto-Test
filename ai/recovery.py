"""Bounded recovery execution with repeat-action protection."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from models.ai_result import RecoveryAction
from models.recovery_result import RecoveryResult, RecoveryStatus


class RecoveryManager:
    def __init__(self, max_steps: int = 3) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        self.max_steps = max_steps

    def recover(
        self,
        decide_action: Callable[[int, list[str]], RecoveryAction],
        handlers: Mapping[RecoveryAction, Callable[[], None]],
        verify: Callable[[], bool],
    ) -> RecoveryResult:
        errors: list[str] = []
        previous_action: RecoveryAction | None = None
        attempts = 0
        for step in range(self.max_steps):
            action = decide_action(step, list(errors))
            if action is RecoveryAction.STOP:
                return RecoveryResult(status=RecoveryStatus.STOPPED, attempts=attempts, last_action=action, reason="AI requested a stop", errors=errors)
            if action is RecoveryAction.HUMAN_INTERVENTION:
                return RecoveryResult(status=RecoveryStatus.NEEDS_HUMAN, attempts=attempts, last_action=action, reason="AI requested human intervention", errors=errors)
            if action == previous_action:
                return RecoveryResult(status=RecoveryStatus.NEEDS_HUMAN, attempts=attempts, last_action=action, reason="Repeated recovery action stopped by the safety limit", errors=errors)
            handler = handlers.get(action)
            if handler is None:
                return RecoveryResult(status=RecoveryStatus.NEEDS_HUMAN, attempts=attempts, last_action=action, reason=f"No handler is registered for {action.value}", errors=errors)
            attempts += 1
            try:
                handler()
                if verify():
                    return RecoveryResult(status=RecoveryStatus.COMPLETED, attempts=attempts, last_action=action, reason="Recovery succeeded", errors=errors)
            except Exception as exc:
                errors.append(f"{action.value}: {type(exc).__name__}: {str(exc)[:300]}")
            previous_action = action
        return RecoveryResult(
            status=RecoveryStatus.NEEDS_HUMAN,
            attempts=attempts,
            last_action=previous_action,
            reason=f"Recovery limit reached ({self.max_steps}); automatic attempts stopped",
            errors=errors,
        )
