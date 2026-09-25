"""在单个测试步骤内执行受限的 AI 分析与恢复。"""

from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from ai.context import FailureContextCollector
from ai.recovery import RecoveryManager
from ai.trace import AITraceLogger
from devices.tv import AndroidTV
from devices.ui import UIDriver
from models.ai_result import AIAnalysisResult, RecoveryAction
from models.recovery_result import RecoveryStatus


class AIRecoveryError(RuntimeError):
    """步骤无法安全恢复时提供可读的失败信息。"""

    def __init__(
        self,
        *,
        step_name: str,
        original_error: Exception,
        reason: str,
        suggested_action: RecoveryAction | None,
        recovery_attempts: int,
        analysis: AIAnalysisResult | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.step_name = step_name
        self.original_error = original_error
        self.reason = reason
        self.suggested_action = suggested_action
        self.recovery_attempts = recovery_attempts
        self.analysis = analysis
        self.context = context
        action = suggested_action.value if suggested_action is not None else "unavailable"
        super().__init__(
            f"Step '{step_name}' failed: {type(original_error).__name__}: {original_error}; "
            f"AI reason: {reason}; suggested action: {action}; "
            f"recovery attempts: {recovery_attempts}"
        )


class AIExecutor:
    def __init__(
        self,
        analyzer: Any,
        context_collector: FailureContextCollector,
        recovery_manager: RecoveryManager,
        tv: AndroidTV,
        ui: UIDriver,
        min_recovery_confidence: float = 0.75,
        navigate_confidence: float = 0.70,
        back_confidence: float = 0.55,
        scroll_confidence: float = 0.45,
        trace_path: str | Path | None = None,
    ) -> None:
        """注入共享分析器、设备对象和受限恢复管理器。"""
        for threshold in (min_recovery_confidence, navigate_confidence, back_confidence, scroll_confidence):
            if not 0 <= threshold <= 1:
                raise ValueError("confidence thresholds must be between 0 and 1")
        self.analyzer = analyzer
        self.context_collector = context_collector
        self.recovery_manager = recovery_manager
        self.tv = tv
        self.ui = ui
        self.min_recovery_confidence = min_recovery_confidence
        self.action_confidence = {
            RecoveryAction.NAVIGATE: navigate_confidence,
            RecoveryAction.BACK: back_confidence,
            RecoveryAction.SCROLL: scroll_confidence,
        }
        default_trace_path = Path(getattr(ui, "reports_dir", "reports")) / "logs" / "ai_recovery.jsonl"
        self.trace_path = Path(trace_path or default_trace_path)
        self.trace_logger = AITraceLogger(self.trace_path)

    def run_step(
        self,
        *,
        name: str,
        goal: str | None = None,
        action: Callable[[], Any],
        verify: Callable[[], bool] | None = None,
        retry_action: Callable[[], Any] | None = None,
        reenter_page: Callable[[], None] | None = None,
        back_retries_action: bool = True,
        max_recovery_steps: int | None = None,
    ) -> Any:
        """运行测试动作；失败时按 AI 建议执行有限且白名单内的恢复。"""
        original_error: Exception
        latest_error: Exception | None = None
        latest_analysis: AIAnalysisResult | None = None
        latest_context: dict[str, Any] | None = None
        latest_result: Any = None
        recovery_attempts = 0
        navigation_safety_error: str | None = None
        action_history: list[dict[str, str]] = []
        observed_states: list[dict[str, Any]] = []
        starting_state: dict[str, Any] | None = None
        run_id = uuid4().hex

        def trace(event: str, **details: Any) -> None:
            self.trace_logger.record(run_id=run_id, step_name=name, event=event, **details)

        trace(
            "step_started",
            goal=goal or name,
            recovery_limit=max_recovery_steps or self.recovery_manager.max_steps,
            minimum_confidence=self.min_recovery_confidence,
        )

        def execute(callback: Callable[[], Any]) -> Any:
            nonlocal latest_error, latest_result
            try:
                latest_result = callback()
                trace("action_completed", result=latest_result)
                return latest_result
            except Exception as exc:
                latest_error = exc
                trace("action_failed", error_type=type(exc).__name__, error=str(exc)[:1000])
                raise

        def verify_step() -> bool:
            nonlocal latest_error
            try:
                passed = verify is None or bool(verify())
            except Exception as exc:
                latest_error = exc
                raise
            if not passed:
                latest_error = RuntimeError("Step verification failed")
            trace("verification", passed=passed)
            return passed

        try:
            initial_result = execute(action)
            if verify_step():
                trace("step_completed", recovery_attempts=0, log_path=str(self.trace_logger.path))
                return initial_result
        except Exception as exc:
            latest_error = exc

        original_error = latest_error or RuntimeError("Step failed without an exception")

        def decide_action(attempt: int, previous_errors: list[str]) -> RecoveryAction:
            nonlocal latest_analysis, latest_context, starting_state
            if navigation_safety_error is not None:
                return RecoveryAction.HUMAN_INTERVENTION
            current_error = latest_error or original_error
            latest_context = self.context_collector.collect(
                step_name=name,
                error=current_error,
                attempt=attempt + 1,
                previous_errors=previous_errors,
            )
            latest_context["goal"] = goal or name
            latest_context["previous_actions"] = list(action_history)
            current_state = {
                "current_activity": latest_context.get("current_activity"),
                "ui_texts": list(latest_context.get("ui_texts", []))[:30],
            }
            if starting_state is None:
                starting_state = current_state
            latest_context["starting_state"] = starting_state
            observed_states.append(current_state)
            latest_context["observed_states"] = list(observed_states)
            try:
                latest_analysis = self.analyzer.analyze(
                    failure=f"{type(current_error).__name__}: {current_error}",
                    context=latest_context,
                )
            except Exception as exc:
                trace(
                    "ai_analysis_failed",
                    attempt=attempt + 1,
                    current_activity=latest_context.get("current_activity"),
                    visible_ui_texts=latest_context.get("ui_texts", []),
                    error_type=type(exc).__name__,
                    error=str(exc)[:1000],
                )
                raise
            # 返回键只关闭当前层级，风险低于点击菜单；仍要求模型明确选择 back。
            minimum_confidence = self.action_confidence.get(
                latest_analysis.suggested_action, self.min_recovery_confidence
            )
            accepted = latest_analysis.confidence >= minimum_confidence
            print(
                f"AI 决策摘要 [{attempt + 1}]：目标={goal or name}；"
                f"页面={latest_context.get('current_activity')}；"
                f"判断={'；'.join(latest_analysis.decision_steps) or latest_analysis.reason}；"
                f"证据={'；'.join(latest_analysis.evidence)}；"
                f"建议={latest_analysis.suggested_action.value}；"
                f"目标项={latest_analysis.target_text}；"
                f"置信度={latest_analysis.confidence:.2f}；"
                f"执行={'是' if accepted else '否（置信度不足）'}",
                flush=True,
            )
            trace(
                "ai_decision",
                attempt=attempt + 1,
                failure=f"{type(current_error).__name__}: {current_error}"[:1000],
                current_activity=latest_context.get("current_activity"),
                goal=goal or name,
                starting_state=starting_state,
                visible_ui_texts=latest_context.get("ui_texts", []),
                scrollable_nodes=latest_context.get("scrollable_nodes", []),
                ui_dump_path=latest_context.get("ui_dump_path"),
                previous_actions=list(action_history),
                observed_states=list(observed_states),
                reason=latest_analysis.reason,
                decision_steps=latest_analysis.decision_steps,
                evidence=latest_analysis.evidence,
                suggested_action=latest_analysis.suggested_action.value,
                target_text=latest_analysis.target_text,
                scroll_direction=latest_analysis.scroll_direction,
                confidence=latest_analysis.confidence,
                confidence_threshold=minimum_confidence,
                accepted=accepted,
            )
            return latest_analysis.suggested_action if accepted else RecoveryAction.HUMAN_INTERVENTION

        def retry_original() -> None:
            nonlocal recovery_attempts
            recovery_attempts += 1
            trace("action_started", action="retry")
            action_history.append({"action": "retry"})
            execute(retry_action or action)

        def go_back_and_retry() -> None:
            nonlocal recovery_attempts
            recovery_attempts += 1
            trace("action_started", action="back")
            self.ui.back()
            action_history.append({"action": "back"})
            trace("action_completed", action="back")
            if back_retries_action:
                execute(retry_action or action)

        def reenter_and_retry() -> None:
            nonlocal recovery_attempts
            recovery_attempts += 1
            trace("action_started", action="reenter_page")
            if reenter_page is None:
                raise RuntimeError("REENTER_PAGE requires an explicit reenter_page callback")
            reenter_page()
            action_history.append({"action": "reenter_page"})
            trace("action_completed", action="reenter_page")
            execute(action)

        def refresh_and_refind() -> None:
            nonlocal recovery_attempts
            recovery_attempts += 1
            trace("action_started", action="refind_element")
            self.ui.dump_ui()
            action_history.append({"action": "refind_element"})
            trace("action_completed", action="refind_element")
            execute(action)

        def navigate_to_target() -> None:
            nonlocal recovery_attempts, navigation_safety_error
            target = latest_analysis.target_text if latest_analysis is not None else None
            visible_texts = (latest_context or {}).get("ui_texts", [])
            if not target or target not in visible_texts:
                navigation_safety_error = "AI navigation target must be visible in the current UI"
                trace("action_rejected", action="navigate", target_text=target, reason=navigation_safety_error)
                raise RuntimeError(navigation_safety_error)
            recovery_attempts += 1
            trace("action_started", action="navigate", target_text=target)
            action_history.append({"action": "navigate", "target_text": target})
            execute(lambda: self.ui.click_text(target, timeout=3))

        def scroll_screen() -> None:
            nonlocal recovery_attempts
            direction = latest_analysis.scroll_direction if latest_analysis is not None else None
            if direction not in {"up", "down"}:
                raise RuntimeError("AI scroll action requires direction up or down")
            recovery_attempts += 1
            trace("action_started", action="scroll", direction=direction)
            self.ui.scroll(direction)
            action_history.append({"action": "scroll", "direction": direction})
            trace("action_completed", action="scroll", direction=direction)

        def action_identity(action: RecoveryAction) -> str:
            context = latest_context or {}
            state = {
                "activity": context.get("current_activity"),
                "elements": context.get("ui_elements"),
                "texts": context.get("ui_texts"),
            }
            observed = any(state.values())
            state_hash = sha256(json.dumps(state, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16] if observed else "unknown"
            if action is RecoveryAction.NAVIGATE and latest_analysis is not None:
                return f"navigate:{latest_analysis.target_text}:{state_hash}"
            if action is RecoveryAction.SCROLL and latest_analysis is not None:
                return f"scroll:{latest_analysis.scroll_direction}:{state_hash}"
            return f"{action.value}:{state_hash}"

        handlers: dict[RecoveryAction, Callable[[], None]] = {
            RecoveryAction.RETRY: retry_original,
            RecoveryAction.BACK: go_back_and_retry,
            RecoveryAction.REFIND_ELEMENT: refresh_and_refind,
            RecoveryAction.NAVIGATE: navigate_to_target,
            RecoveryAction.SCROLL: scroll_screen,
        }
        if reenter_page is not None:
            handlers[RecoveryAction.REENTER_PAGE] = reenter_and_retry

        try:
            recovery = self.recovery_manager.recover(
                decide_action=decide_action,
                handlers=handlers,
                verify=verify_step,
                action_identity=action_identity,
                max_steps=max_recovery_steps,
            )
        except Exception as exc:
            if latest_analysis is None:
                reason = f"AI analysis failed: {type(exc).__name__}: {exc}"
            else:
                reason = f"Recovery decision failed: {type(exc).__name__}: {exc}"
            trace("step_failed", reason=reason, recovery_attempts=recovery_attempts, log_path=str(self.trace_logger.path))
            raise AIRecoveryError(
                step_name=name,
                original_error=original_error,
                reason=reason,
                suggested_action=latest_analysis.suggested_action if latest_analysis else None,
                recovery_attempts=recovery_attempts,
                analysis=latest_analysis,
                context=latest_context,
            ) from exc

        if recovery.status is RecoveryStatus.COMPLETED:
            trace("step_completed", recovery_attempts=recovery.attempts, log_path=str(self.trace_logger.path))
            return latest_result

        required_confidence = self.action_confidence.get(
            latest_analysis.suggested_action, self.min_recovery_confidence
        ) if latest_analysis is not None else self.min_recovery_confidence
        if latest_analysis is not None and latest_analysis.confidence < required_confidence:
            reason = (
                f"AI confidence {latest_analysis.confidence:.2f} is below the required "
                f"threshold {required_confidence:.2f}; human intervention is required"
            )
        elif latest_analysis is not None and recovery.status is RecoveryStatus.STOPPED:
            reason = latest_analysis.reason
        elif latest_analysis is not None and recovery.status is RecoveryStatus.NEEDS_HUMAN:
            reason = f"{latest_analysis.reason}; {recovery.reason}"
        else:
            reason = recovery.reason

        trace(
            "step_failed",
            reason=reason,
            recovery_attempts=recovery.attempts,
            final_action=latest_analysis.suggested_action.value if latest_analysis else None,
            log_path=str(self.trace_logger.path),
        )
        raise AIRecoveryError(
            step_name=name,
            original_error=original_error,
            reason=reason,
            suggested_action=latest_analysis.suggested_action if latest_analysis else recovery.last_action,
            recovery_attempts=recovery.attempts,
            analysis=latest_analysis,
            context=latest_context,
        )
