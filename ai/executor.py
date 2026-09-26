"""在单个测试步骤内执行受限的 AI 分析与恢复。"""

from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from ai.context import FailureContextCollector
from ai.recovery import RecoveryManager
from ai.trace import AITraceLogger
from devices.tv import AndroidTV
from devices.ui import DeviceTransportError, UIDriver
from models.ai_result import AIAnalysisResult, RecoveryAction
from models.recovery_result import RecoveryStatus


def _application_elements(context: dict[str, Any]) -> list[dict[str, Any]]:
    """只保留当前应用节点，排除系统状态栏的动态内容。"""
    activity = context.get("current_activity") or ""
    app_package = activity.split("/", maxsplit=1)[0]
    return [
        element for element in context.get("ui_elements", [])
        if element.get("package") != "com.android.systemui"
        and (not app_package or not element.get("package") or element.get("package") == app_package)
    ]


def build_state_fingerprint(context: dict[str, Any]) -> str:
    """用稳定的应用页面字段生成导航及动作熔断共用的指纹。"""
    elements = [
        {key: element.get(key) for key in ("text", "content_desc", "resource_id", "clickable", "scrollable")}
        for element in _application_elements(context)
    ]
    elements.sort(key=lambda element: json.dumps(element, ensure_ascii=False, sort_keys=True))
    state = {"current_activity": context.get("current_activity") or "", "elements": elements}
    if not state["current_activity"] and not elements:
        return "unknown"
    return sha256(json.dumps(state, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


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
        ui_evidence_error: str | None = None
        action_history: list[dict[str, str]] = []
        observed_states: list[dict[str, Any]] = []
        starting_state: dict[str, Any] | None = None
        visited_edges: set[tuple[str, str]] = set()
        navigation_stack: list[dict[str, str]] = []
        current_state_fingerprint = "unknown"
        pending_back_state: str | None = None
        pending_scroll_state: str | None = None
        run_id = uuid4().hex

        def trace(event: str, **details: Any) -> None:
            self.trace_logger.record(
                run_id=run_id, step_name=name, event=event,
                state_fingerprint=current_state_fingerprint,
                blocked_targets=sorted(target for state, target in visited_edges if state == current_state_fingerprint),
                visited_edges_count=len(visited_edges),
                navigation_stack=list(navigation_stack),
                **details,
            )

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
        if isinstance(original_error, DeviceTransportError):
            trace("step_failed", reason=str(original_error), failure_category="device_transport",
                  recovery_attempts=0, log_path=str(self.trace_logger.path))
            raise original_error

        def capture_final_screenshot() -> None:
            if latest_context is None:
                return
            try:
                latest_context["final_screenshot_path"] = str(self.ui.take_screenshot())
            except Exception as exc:
                latest_context["final_screenshot_error"] = f"{type(exc).__name__}: {str(exc)[:300]}"

        def decide_action(attempt: int, previous_errors: list[str]) -> RecoveryAction:
            nonlocal latest_analysis, latest_context, starting_state, current_state_fingerprint, pending_back_state, pending_scroll_state, navigation_safety_error, ui_evidence_error
            if navigation_safety_error is not None:
                return RecoveryAction.HUMAN_INTERVENTION
            current_error = latest_error or original_error
            latest_context = self.context_collector.collect(
                step_name=name,
                error=current_error,
                attempt=attempt + 1,
                previous_errors=previous_errors,
                include_screenshot=attempt == 0,
            )
            current_state_fingerprint = build_state_fingerprint(latest_context)
            transport_recovery = latest_context.get("transport_recovery")
            if transport_recovery:
                trace(
                    "transport_recovery", attempt=attempt + 1,
                    error_type=transport_recovery.get("error_type"),
                    uiautomator_reconnect=transport_recovery.get("reconnected", False),
                    uiautomator_retry_success=transport_recovery.get("u2_retry_success", False),
                    adb_fallback_used=transport_recovery.get("adb_fallback_used", False),
                    adb_fallback_success=transport_recovery.get("adb_fallback_success", False),
                    ui_dump_backend=latest_context.get("ui_dump_backend"),
                )
            if latest_context.get("transport_unavailable"):
                raise DeviceTransportError(
                    "Device transport unavailable after uiautomator2 reconnect and ADB fallback"
                )
            if latest_context.get("ui_dump_error") or latest_context.get("ui_hierarchy_error"):
                ui_evidence_error = (
                    f"UI hierarchy evidence unavailable: "
                    f"{latest_context.get('ui_dump_error') or latest_context.get('ui_hierarchy_error')}"
                )
                trace("ui_evidence_unavailable", attempt=attempt + 1, reason=ui_evidence_error)
                return RecoveryAction.HUMAN_INTERVENTION
            scroll_at_boundary = pending_scroll_state is not None and current_state_fingerprint == pending_scroll_state
            pending_scroll_state = None
            if pending_back_state is not None:
                if (navigation_stack and current_state_fingerprint == navigation_stack[-1]["parent_state"]
                        and current_state_fingerprint != pending_back_state):
                    navigation_stack.pop()
                pending_back_state = None
            blocked_targets = sorted(target for state, target in visited_edges if state == current_state_fingerprint)
            candidates = list(dict.fromkeys(
                label
                for element in _application_elements(latest_context) if element.get("clickable")
                for label in (element.get("text"), element.get("content_desc")) if label and label not in blocked_targets
            ))
            latest_context["available_navigation_candidates"] = candidates
            latest_context["navigation"] = {
                "current_state_id": current_state_fingerprint,
                "visited_edges": [
                    {"from_state": state, "target": target} for state, target in sorted(visited_edges)
                ],
                "blocked_targets_on_current_page": blocked_targets,
                "navigation_stack": [dict(edge) for edge in navigation_stack],
                "scroll_at_boundary": scroll_at_boundary,
                "branch_exhausted": bool(navigation_stack and not candidates
                                         and (scroll_at_boundary or not latest_context.get("scrollable_nodes"))),
            }
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
            analysis_started = perf_counter()
            try:
                latest_analysis = self.analyzer.analyze(
                    failure=f"{type(current_error).__name__}: {current_error}",
                    context=latest_context,
                )
            except Exception as exc:
                analysis_duration_ms = round((perf_counter() - analysis_started) * 1000, 2)
                trace(
                    "ai_analysis_failed",
                    attempt=attempt + 1,
                    analysis_duration_ms=analysis_duration_ms,
                    current_activity=latest_context.get("current_activity"),
                    visible_ui_texts=latest_context.get("ui_texts", []),
                    error_type=type(exc).__name__,
                    error=str(exc)[:1000],
                )
                raise
            analysis_duration_ms = round((perf_counter() - analysis_started) * 1000, 2)
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
                f"请求耗时={analysis_duration_ms:.2f}ms；"
                f"执行={'是' if accepted else '否（置信度不足）'}",
                flush=True,
            )
            trace(
                "ai_decision",
                attempt=attempt + 1,
                failure=f"{type(current_error).__name__}: {current_error}"[:1000],
                current_activity=latest_context.get("current_activity"),
                analysis_duration_ms=analysis_duration_ms,
                goal=goal or name,
                starting_state=starting_state,
                visible_ui_texts=latest_context.get("ui_texts", []),
                scrollable_nodes=latest_context.get("scrollable_nodes", []),
                ui_dump_path=latest_context.get("ui_dump_path"),
                ui_dump_backend=latest_context.get("ui_dump_backend"),
                ui_dump_error=latest_context.get("ui_dump_error"),
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
            if accepted and latest_analysis.suggested_action is RecoveryAction.NAVIGATE:
                target = latest_analysis.target_text
                if (current_state_fingerprint, target) in visited_edges:
                    navigation_safety_error = "target already explored on current page"
                    trace("action_rejected", action="navigate", target_text=target, from_state=current_state_fingerprint,
                          reason="visited_edge", detail=navigation_safety_error, edge_added=False)
                    return RecoveryAction.HUMAN_INTERVENTION
            return latest_analysis.suggested_action if accepted else RecoveryAction.HUMAN_INTERVENTION

        def retry_original() -> None:
            nonlocal recovery_attempts
            recovery_attempts += 1
            trace("action_started", action="retry")
            action_history.append({"action": "retry"})
            execute(retry_action or action)

        def go_back_and_retry() -> None:
            nonlocal recovery_attempts, pending_back_state
            recovery_attempts += 1
            trace("action_started", action="back")
            self.ui.back()
            pending_back_state = current_state_fingerprint
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
            edge = (current_state_fingerprint, target)
            if edge in visited_edges:
                navigation_safety_error = "target already explored on current page"
                trace("action_rejected", action="navigate", target_text=target, from_state=current_state_fingerprint,
                      reason="visited_edge", detail=navigation_safety_error, edge_added=False)
                raise RuntimeError(navigation_safety_error)
            if not target or target not in (latest_context or {}).get("available_navigation_candidates", []):
                navigation_safety_error = "AI navigation target must be a visible clickable candidate"
                trace("action_rejected", action="navigate", target_text=target, from_state=current_state_fingerprint,
                      reason="target_not_available", detail=navigation_safety_error, edge_added=False)
                raise RuntimeError(navigation_safety_error)
            recovery_attempts += 1
            trace("action_started", action="navigate", target_text=target, from_state=current_state_fingerprint)
            action_history.append({"action": "navigate", "target_text": target})
            def click_candidate() -> bool:
                if not self.ui.click_visible_label(target, timeout=3):
                    raise RuntimeError("Android UI navigation click did not succeed")
                return True

            execute(click_candidate)
            visited_edges.add((current_state_fingerprint, target))
            navigation_stack.append({"parent_state": current_state_fingerprint, "target": target})
            trace("navigation_edge_added", action="navigate", from_state=current_state_fingerprint,
                  target=target, edge_added=True)

        def scroll_screen() -> None:
            nonlocal recovery_attempts, pending_scroll_state
            direction = latest_analysis.scroll_direction if latest_analysis is not None else None
            if direction not in {"up", "down"}:
                raise RuntimeError("AI scroll action requires direction up or down")
            recovery_attempts += 1
            trace("action_started", action="scroll", direction=direction)
            self.ui.scroll(direction)
            pending_scroll_state = current_state_fingerprint
            action_history.append({"action": "scroll", "direction": direction})
            trace("action_completed", action="scroll", direction=direction)

        def action_identity(action: RecoveryAction) -> str:
            if action is RecoveryAction.NAVIGATE and latest_analysis is not None:
                return f"navigate:{latest_analysis.target_text}:{current_state_fingerprint}"
            if action is RecoveryAction.SCROLL and latest_analysis is not None:
                return f"scroll:{latest_analysis.scroll_direction}:{current_state_fingerprint}"
            return f"{action.value}:{current_state_fingerprint}"

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
                fatal_exceptions=(DeviceTransportError,),
            )
        except DeviceTransportError as exc:
            trace("step_failed", reason=str(exc), failure_category="device_transport",
                  recovery_attempts=recovery_attempts, log_path=str(self.trace_logger.path))
            raise
        except Exception as exc:
            if latest_analysis is None:
                reason = f"AI analysis failed: {type(exc).__name__}: {exc}"
            else:
                reason = f"Recovery decision failed: {type(exc).__name__}: {exc}"
            capture_final_screenshot()
            trace("step_failed", reason=reason, recovery_attempts=recovery_attempts,
                  final_screenshot_path=(latest_context or {}).get("final_screenshot_path"),
                  log_path=str(self.trace_logger.path))
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
        if ui_evidence_error is not None:
            reason = f"{ui_evidence_error}; human intervention is required"
        elif latest_analysis is not None and latest_analysis.confidence < required_confidence:
            reason = (
                f"AI confidence {latest_analysis.confidence:.2f} is below the required "
                f"threshold {required_confidence:.2f}; human intervention is required"
            )
        elif latest_analysis is not None and recovery.status is RecoveryStatus.STOPPED:
            reason = latest_analysis.reason
        elif latest_analysis is not None and recovery.status is RecoveryStatus.NEEDS_HUMAN:
            reason = f"{latest_analysis.reason}; {navigation_safety_error or recovery.reason}; human intervention is required"
        else:
            reason = recovery.reason

        capture_final_screenshot()
        trace(
            "step_failed",
            reason=reason,
            recovery_attempts=recovery.attempts,
            final_action=latest_analysis.suggested_action.value if latest_analysis else None,
            final_screenshot_path=(latest_context or {}).get("final_screenshot_path"),
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
