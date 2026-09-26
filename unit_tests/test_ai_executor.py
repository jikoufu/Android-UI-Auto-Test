import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai.context import FailureContextCollector
from ai.executor import AIExecutor, AIRecoveryError, build_state_fingerprint
from ai.recovery import RecoveryManager
from ai.tools import build_device_tools
from devices.ui import DeviceTransportError
from models.ai_result import AIAnalysisResult, RecoveryAction
from models.device_state import DeviceState


class FakeAnalyzer:
    def __init__(self, *results: AIAnalysisResult) -> None:
        """按顺序返回预置的结构化分析结果。"""
        self.results = list(results)
        self.calls: list[dict[str, object]] = []

    def analyze(self, *, failure: str, context: dict[str, object]) -> AIAnalysisResult:
        """记录失败信息和现场，并返回下一条预置结果。"""
        self.calls.append({"failure": failure, "context": context})
        return self.results.pop(0)


class FakeTV:
    def current_activity(self) -> str:
        """提供固定的前台页面名称。"""
        return "com.android.settings/.Settings"

    def get_device_state(self) -> DeviceState:
        """提供结构化的假设备状态。"""
        return DeviceState(serial="fake-device", model="Fake Android")


class FakeUI:
    def __init__(self, dump_path: Path | None = None, screenshot_error: Exception | None = None) -> None:
        """记录按键和 hierarchy 操作，并可模拟截图失败。"""
        self.dump_path = dump_path
        self.screenshot_error = screenshot_error
        self.back_calls = 0
        self.dump_calls = 0
        self.screenshot_calls = 0
        self.clicked_texts: list[str] = []
        self.scroll_directions: list[str] = []

    def click_text(self, text: str, timeout: float = 10) -> bool:
        """记录按文字点击，模拟导航目标执行。"""
        self.clicked_texts.append(text)
        return True

    def click_visible_label(self, label: str, timeout: float = 10) -> bool:
        """记录按可见文字或描述点击导航目标。"""
        self.clicked_texts.append(label)
        return True

    def back(self) -> None:
        """记录一次返回键操作。"""
        self.back_calls += 1

    def scroll(self, direction: str) -> None:
        """记录一次纵向滚动。"""
        self.scroll_directions.append(direction)

    def dump_ui(self) -> Path:
        """返回假 hierarchy 文件路径。"""
        self.dump_calls += 1
        if self.dump_path is None:
            raise RuntimeError("No fake UI dump configured")
        return self.dump_path

    def take_screenshot(self) -> Path:
        """返回截图路径，或按配置模拟截图失败。"""
        self.screenshot_calls += 1
        if self.screenshot_error is not None:
            raise self.screenshot_error
        return Path("reports/fake.png")


class FakeContextCollector:
    def __init__(
        self,
        ui_texts: list[list[str]] | None = None,
        ui_elements: list[list[dict[str, object]]] | None = None,
        current_activities: list[str] | None = None,
    ) -> None:
        """收集执行器传入的失败步骤元数据。"""
        self.calls: list[dict[str, object]] = []
        self.ui_texts = list(ui_texts or [])
        self.ui_elements = list(ui_elements or [])
        self.current_activities = list(current_activities or [])

    def collect(self, **kwargs: object) -> dict[str, object]:
        """记录采集请求并返回可供假分析器读取的上下文。"""
        self.calls.append(kwargs)
        texts = self.ui_texts.pop(0) if self.ui_texts else []
        elements = self.ui_elements.pop(0) if self.ui_elements else [
            {"text": text, "content_desc": "", "resource_id": "", "clickable": True, "scrollable": False}
            for text in texts
        ]
        activity = self.current_activities.pop(0) if self.current_activities else None
        return {
            "step_name": kwargs["step_name"],
            "attempt": kwargs["attempt"],
            "current_activity": activity,
            "ui_texts": texts,
            "ui_elements": elements,
            "scrollable_nodes": [element for element in elements if element.get("scrollable")],
        }


def _analysis(
    action: RecoveryAction,
    confidence: float = 0.9,
    target_text: str | None = None,
) -> AIAnalysisResult:
    """构造一个最小合法的结构化恢复建议。"""
    return AIAnalysisResult(
        error_type="ui_step_failed",
        reason="Fake evidence based decision",
        suggested_action=action,
        confidence=confidence,
        evidence=["fake evidence"],
        decision_steps=["现场显示目标缺失", "当前动作可以安全重试"],
        target_text=target_text,
    )


def _executor(
    analyzer: FakeAnalyzer,
    ui: FakeUI | None = None,
    collector: FakeContextCollector | FailureContextCollector | None = None,
    max_steps: int = 3,
    min_confidence: float = 0.75,
    trace_path: Path | None = None,
) -> AIExecutor:
    """组合单元测试用依赖，不连接真实设备或 AI 服务。"""
    fake_ui = ui or FakeUI()
    return AIExecutor(
        analyzer=analyzer,
        context_collector=collector or FakeContextCollector(),
        recovery_manager=RecoveryManager(max_steps=max_steps),
        tv=FakeTV(),
        ui=fake_ui,
        min_recovery_confidence=min_confidence,
        trace_path=trace_path,
    )


def test_step_success_does_not_call_ai():
    """验证动作和校验均成功时直接返回且不请求 AI。"""
    analyzer = FakeAnalyzer()
    executor = _executor(analyzer)

    # Step 1：执行成功动作并确认校验通过。
    result = executor.run_step(name="成功步骤", action=lambda: "done", verify=lambda: True)

    assert result == "done"
    assert analyzer.calls == []


def test_failed_action_can_retry_and_return_second_result():
    """验证动作首次失败时可由高置信度 RETRY 建议恢复。"""
    analyzer = FakeAnalyzer(_analysis(RecoveryAction.RETRY))
    executor = _executor(analyzer)
    calls = 0

    # Step 1：让动作首次失败，供执行器采集现场并分析。
    def action() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary UI failure")
        return "recovered"

    result = executor.run_step(name="重试步骤", action=action)

    assert result == "recovered"
    assert calls == 2
    assert len(analyzer.calls) == 1


def test_failed_verification_can_back_then_retry():
    """验证校验失败时 BACK 建议会返回并重新执行步骤。"""
    analyzer = FakeAnalyzer(_analysis(RecoveryAction.BACK))
    ui = FakeUI()
    executor = _executor(analyzer, ui=ui)
    actions = 0
    verifications = 0

    # Step 1：首次校验失败，恢复后再次确认步骤结果。
    def action() -> int:
        nonlocal actions
        actions += 1
        return actions

    def verify() -> bool:
        nonlocal verifications
        verifications += 1
        return verifications > 1

    result = executor.run_step(name="返回后重试", action=action, verify=verify)

    assert result == 2
    assert actions == 2
    assert ui.back_calls == 1


def test_reenter_page_requires_and_runs_explicit_callback():
    """验证 REENTER_PAGE 只使用调用方提供的重新进入流程。"""
    analyzer = FakeAnalyzer(_analysis(RecoveryAction.REENTER_PAGE))
    executor = _executor(analyzer)
    action_calls = 0
    reenter_calls = 0

    # Step 1：动作首次失败，恢复时调用显式页面入口后重试原动作。
    def action() -> str:
        nonlocal action_calls
        action_calls += 1
        if action_calls == 1:
            raise RuntimeError("page was not ready")
        return "entered"

    def reenter_page() -> None:
        nonlocal reenter_calls
        reenter_calls += 1

    result = executor.run_step(name="重新进入页面", action=action, reenter_page=reenter_page)

    assert result == "entered"
    assert reenter_calls == 1
    assert action_calls == 2


def test_refind_element_refreshes_ui_before_retry(tmp_path):
    """验证 REFIND_ELEMENT 刷新 UI hierarchy 后重试原动作。"""
    dump_path = tmp_path / "window.xml"
    dump_path.write_text("<hierarchy />", encoding="utf-8")
    ui = FakeUI(dump_path=dump_path)
    executor = _executor(FakeAnalyzer(_analysis(RecoveryAction.REFIND_ELEMENT)), ui=ui)
    action_calls = 0

    # Step 1：动作首次失败，恢复时刷新界面并重新定位执行。
    def action() -> str:
        nonlocal action_calls
        action_calls += 1
        if action_calls == 1:
            raise RuntimeError("element not found")
        return "found"

    result = executor.run_step(name="重新定位元素", action=action)

    assert result == "found"
    assert ui.dump_calls == 1
    assert action_calls == 2


def test_low_confidence_requires_human_without_running_recovery():
    """验证低于置信度门槛时不执行 AI 建议动作。"""
    analyzer = FakeAnalyzer(_analysis(RecoveryAction.RETRY, confidence=0.5))
    executor = _executor(analyzer)
    action_calls = 0

    # Step 1：制造失败并确认低置信度结果立即转人工处理。
    def action() -> None:
        nonlocal action_calls
        action_calls += 1
        raise RuntimeError("persistent failure")

    with pytest.raises(AIRecoveryError, match="below the required threshold") as error:
        executor.run_step(name="低置信度步骤", action=action)

    assert error.value.recovery_attempts == 0
    assert action_calls == 1


def test_human_intervention_action_stops_without_device_action():
    """验证 AI 请求人工介入时不会触发设备恢复动作。"""
    analyzer = FakeAnalyzer(_analysis(RecoveryAction.HUMAN_INTERVENTION))
    ui = FakeUI()
    executor = _executor(analyzer, ui=ui)

    # Step 1：失败后接收人工介入建议，并确认未发送返回键。
    with pytest.raises(AIRecoveryError, match="human intervention"):
        executor.run_step(name="等待人工步骤", action=lambda: (_ for _ in ()).throw(RuntimeError("failed")))

    assert ui.back_calls == 0


def test_recovery_stops_at_configured_max_steps():
    """验证恢复管理器达到最大恢复步数后停止。"""
    analyzer = FakeAnalyzer(_analysis(RecoveryAction.RETRY))
    executor = _executor(analyzer, max_steps=1)
    verifications = 0

    # Step 1：让重试后的校验仍失败，检查最大恢复步数限制。
    def verify() -> bool:
        nonlocal verifications
        verifications += 1
        return False

    with pytest.raises(AIRecoveryError, match="limit reached") as error:
        executor.run_step(name="达到恢复上限", action=lambda: None, verify=verify)

    assert error.value.recovery_attempts == 1
    assert verifications == 2


def test_screenshot_failure_does_not_block_analysis(tmp_path):
    """验证截图采集失败时仍会把 UI XML 交给 AI 分析并恢复。"""
    dump_path = tmp_path / "window.xml"
    dump_path.write_text("<hierarchy>" + ("x" * 100) + "</hierarchy>", encoding="utf-8")
    ui = FakeUI(dump_path=dump_path, screenshot_error=RuntimeError("camera unavailable"))
    collector = FailureContextCollector(FakeTV(), ui, max_ui_chars=30)
    analyzer = FakeAnalyzer(_analysis(RecoveryAction.RETRY))
    executor = _executor(analyzer, ui=ui, collector=collector)
    calls = 0

    # Step 1：首次动作失败，确认 screenshot 异常不会阻止 XML 上下文分析和重试。
    def action() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("missing element")
        return "recovered"

    result = executor.run_step(name="截图失败仍可分析", action=action)
    context = analyzer.calls[0]["context"]

    assert result == "recovered"
    assert context["ui_hierarchy_truncated"] is True
    assert context["screenshot_path"] is None
    assert "camera unavailable" in context["screenshot_error"]
    assert context["ui_dump_path"] == str(dump_path)


def test_ai_can_scroll_then_navigate_to_visible_targets():
    """验证 AI 可先滚动查找，再逐级点击当前可见的目标。"""
    analyzer = FakeAnalyzer(
        _analysis(RecoveryAction.SCROLL, confidence=0.6),
        _analysis(RecoveryAction.NAVIGATE, target_text="AI判断出的入口"),
        _analysis(RecoveryAction.NAVIGATE, target_text="开发者选项"),
    )
    ui = FakeUI()
    analyzer.results[0].scroll_direction = "down"
    collector = FakeContextCollector(ui_texts=[[], ["AI判断出的入口"], ["开发者选项"]])
    executor = _executor(analyzer, ui=ui, collector=collector, max_steps=4)

    # Step 1：让首次动作失败，再让 AI 根据两次现场文字逐项导航。
    def verify() -> bool:
        return ui.clicked_texts[-1:] == ["开发者选项"]

    result = executor.run_step(
        name="逐项导航设置",
        action=lambda: (_ for _ in ()).throw(RuntimeError("initial target missing")),
        verify=verify,
    )

    assert ui.scroll_directions == ["down"]
    assert ui.clicked_texts == ["AI判断出的入口", "开发者选项"]
    assert result is True


def test_ai_can_return_scroll_and_follow_goal_without_retrying_old_action():
    """验证目标导航时返回后会重新观察页面，再按目标继续操作。"""
    analyzer = FakeAnalyzer(
        _analysis(RecoveryAction.BACK, confidence=0.6),
        _analysis(RecoveryAction.SCROLL, confidence=0.6),
        _analysis(RecoveryAction.NAVIGATE, target_text="更多设置"),
        _analysis(RecoveryAction.NAVIGATE, target_text="开发者选项"),
    )
    analyzer.results[1].scroll_direction = "down"
    ui = FakeUI()
    collector = FakeContextCollector(
        ui_texts=[[], [], ["更多设置"], ["开发者选项"]]
    )
    executor = _executor(analyzer, ui=ui, collector=collector, max_steps=3)

    # Step 1：模拟从错误页面出发，AI 返回、滚动并根据新层级逐步导航。
    result = executor.run_step(
        name="设置 → 我的设备 → 开发者选项",
        action=lambda: (_ for _ in ()).throw(RuntimeError("target absent on current page")),
        verify=lambda: ui.clicked_texts[-1:] == ["开发者选项"],
        back_retries_action=False,
        max_recovery_steps=4,
    )

    assert ui.back_calls == 1
    assert ui.scroll_directions == ["down"]
    assert ui.clicked_texts == ["更多设置", "开发者选项"]
    assert result is True


def test_ai_navigation_refuses_target_unless_visible():
    """验证 AI 不能点击当前 UI 层级中不可见的目标。"""
    analyzer = FakeAnalyzer(_analysis(RecoveryAction.NAVIGATE, target_text="开发者选项"))
    ui = FakeUI()
    collector = FakeContextCollector(ui_texts=[["更多设置"]])
    executor = _executor(analyzer, ui=ui, collector=collector)

    # Step 1：提供失败现场并确认不安全的导航建议被转交人工处理。
    with pytest.raises(AIRecoveryError, match="human intervention") as error:
        executor.run_step(
            name="拒绝未验证导航",
            action=lambda: (_ for _ in ()).throw(RuntimeError("initial target missing")),
        )

    assert ui.clicked_texts == []
    assert error.value.recovery_attempts == 1


def test_two_backs_on_different_pages_are_allowed():
    """验证不同页面连续返回不会被重复动作熔断。"""
    analyzer = FakeAnalyzer(_analysis(RecoveryAction.BACK), _analysis(RecoveryAction.BACK))
    ui = FakeUI()
    collector = FakeContextCollector(ui_texts=[["页面 C"], ["页面 B"]])
    executor = _executor(analyzer, ui=ui, collector=collector)

    # Step 1：模拟从页面 C 经过页面 B 返回目标页面 A。
    executor.run_step(
        name="连续返回两级",
        action=lambda: (_ for _ in ()).throw(RuntimeError("wrong page")),
        verify=lambda: ui.back_calls == 2,
        back_retries_action=False,
    )

    # Step 2：确认两个不同页面上的 BACK 都已执行，起点是第一次失败现场。
    assert ui.back_calls == 2
    assert analyzer.calls[1]["context"]["starting_state"]["ui_texts"] == ["页面 C"]


def test_same_action_on_same_page_stops_before_second_back():
    """验证页面无变化时重复返回会触发熔断。"""
    analyzer = FakeAnalyzer(_analysis(RecoveryAction.BACK), _analysis(RecoveryAction.BACK))
    ui = FakeUI()
    collector = FakeContextCollector(ui_texts=[["页面 C"], ["页面 C"]])
    executor = _executor(analyzer, ui=ui, collector=collector)

    # Step 1：模拟 BACK 后页面仍停留在同一状态。
    with pytest.raises(AIRecoveryError, match="Repeated recovery action"):
        executor.run_step(
            name="相同页面返回熔断",
            action=lambda: (_ for _ in ()).throw(RuntimeError("wrong page")),
            verify=lambda: False,
            back_retries_action=False,
        )

    # Step 2：确认第二次 BACK 未发送给设备。
    assert ui.back_calls == 1


def test_focus_and_bounds_changes_do_not_hide_same_page_loop():
    """验证焦点和坐标变化不影响同一页面的重复动作熔断。"""
    analyzer = FakeAnalyzer(_analysis(RecoveryAction.BACK), _analysis(RecoveryAction.BACK))
    ui = FakeUI()
    collector = FakeContextCollector(
        ui_texts=[["系统设置"], ["系统设置"]],
        ui_elements=[
            [
                {"text": "系统设置", "resource_id": "settings/system", "package": "com.android.settings",
                 "clickable": True, "scrollable": False, "bounds": "[0,0][100,100]", "focused": False},
                {"text": "2:31", "resource_id": "status/time", "package": "com.android.systemui"},
                {"text": "1KB/s", "resource_id": "status/network", "package": "com.android.systemui"},
            ],
            [
                {"text": "系统设置", "resource_id": "settings/system", "package": "com.android.settings",
                 "clickable": True, "scrollable": False, "bounds": "[20,20][120,120]", "focused": True},
                {"text": "2:32", "resource_id": "status/time", "package": "com.android.systemui"},
                {"text": "5KB/s", "resource_id": "status/network", "package": "com.android.systemui"},
            ],
        ],
        current_activities=["com.android.settings/.Settings", "com.android.settings/.Settings"],
    )
    executor = _executor(analyzer, ui=ui, collector=collector)

    # Step 1：模拟返回后页面内容不变、只有焦点和节点坐标变化。
    with pytest.raises(AIRecoveryError, match="Repeated recovery action"):
        executor.run_step(
            name="焦点变化重复返回熔断",
            action=lambda: (_ for _ in ()).throw(RuntimeError("wrong page")),
            verify=lambda: False,
            back_retries_action=False,
        )

    # Step 2：确认不稳定的焦点与坐标没有放过重复返回。
    assert ui.back_calls == 1


def test_same_navigation_label_on_different_pages_is_allowed():
    """验证不同页面的同名入口可以各点击一次。"""
    analyzer = FakeAnalyzer(
        _analysis(RecoveryAction.NAVIGATE, target_text="更多"),
        _analysis(RecoveryAction.NAVIGATE, target_text="更多"),
    )
    ui = FakeUI()
    collector = FakeContextCollector(ui_texts=[["页面 A", "更多"], ["页面 B", "更多"]])
    executor = _executor(analyzer, ui=ui, collector=collector)

    # Step 1：模拟两个层级均显示“更多”，并逐层进入。
    executor.run_step(
        name="不同页面同名入口",
        action=lambda: (_ for _ in ()).throw(RuntimeError("target absent")),
        verify=lambda: len(ui.clicked_texts) == 2,
    )

    # Step 2：确认相同文字在不同页面可分别点击。
    assert ui.clicked_texts == ["更多", "更多"]


def test_visited_entry_is_rejected_after_returning_to_parent(tmp_path):
    """验证返回父页面后已进入过的入口被拒绝且不再点击。"""
    analyzer = FakeAnalyzer(
        _analysis(RecoveryAction.NAVIGATE, target_text="我的设备"),
        _analysis(RecoveryAction.BACK),
        _analysis(RecoveryAction.NAVIGATE, target_text="我的设备"),
    )
    ui = FakeUI()
    collector = FakeContextCollector(ui_texts=[
        ["Settings", "我的设备", "系统"], ["我的设备", "设备名称"], ["Settings", "我的设备", "系统"],
    ])
    log_path = tmp_path / "navigation.jsonl"
    executor = _executor(analyzer, ui=ui, collector=collector, max_steps=4, trace_path=log_path)

    # Step 1：模拟进入错误分支、返回父页面，再收到同一入口建议。
    with pytest.raises(AIRecoveryError, match="target already explored"):
        executor.run_step(
            name="拒绝重复分支", action=lambda: (_ for _ in ()).throw(RuntimeError("target missing")),
            verify=lambda: False, back_retries_action=False,
        )

    # Step 2：确认候选过滤、路径出栈和执行前拒绝都生效。
    parent_context = analyzer.calls[2]["context"]
    assert parent_context["navigation"]["blocked_targets_on_current_page"] == ["我的设备"]
    assert parent_context["available_navigation_candidates"] == ["Settings", "系统"]
    assert parent_context["navigation"]["navigation_stack"] == []
    assert ui.clicked_texts == ["我的设备"]
    assert ui.back_calls == 1
    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert any(event["event"] == "action_rejected" and event["reason"] == "visited_edge" for event in events)
    assert any(event["event"] == "navigation_edge_added" and event["edge_added"] for event in events)


def test_other_entry_on_parent_remains_available():
    """验证父页面中已探索入口被屏蔽后仍可进入其他入口。"""
    analyzer = FakeAnalyzer(
        _analysis(RecoveryAction.NAVIGATE, target_text="我的设备"),
        _analysis(RecoveryAction.BACK),
        _analysis(RecoveryAction.NAVIGATE, target_text="系统"),
    )
    ui = FakeUI()
    collector = FakeContextCollector(ui_texts=[
        ["Settings", "我的设备", "系统"], ["我的设备", "设备名称"], ["Settings", "我的设备", "系统"],
    ])
    executor = _executor(analyzer, ui=ui, collector=collector, max_steps=3)

    # Step 1：进入旧分支并返回，再选择父页面中未探索的系统入口。
    executor.run_step(
        name="选择其他分支", action=lambda: (_ for _ in ()).throw(RuntimeError("target missing")),
        verify=lambda: ui.clicked_texts[-1:] == ["系统"], back_retries_action=False,
    )

    # Step 2：确认新入口确实被点击。
    assert ui.clicked_texts == ["我的设备", "系统"]
    assert analyzer.calls[2]["context"]["navigation"]["blocked_targets_on_current_page"] == ["我的设备"]


def test_unchanged_scroll_marks_child_branch_exhausted():
    """验证子页面滚动后无变化时明确提示返回父页面。"""
    analyzer = FakeAnalyzer(
        _analysis(RecoveryAction.NAVIGATE, target_text="我的设备"),
        _analysis(RecoveryAction.SCROLL, confidence=0.6),
        _analysis(RecoveryAction.BACK),
    )
    analyzer.results[1].scroll_direction = "down"
    child_elements = [{"text": "", "content_desc": "", "resource_id": "device/list",
                       "clickable": False, "scrollable": True}]
    collector = FakeContextCollector(
        ui_texts=[["我的设备"], [], []],
        ui_elements=[
            [{"text": "我的设备", "clickable": True, "scrollable": False}],
            child_elements, child_elements,
        ],
    )
    ui = FakeUI()
    executor = _executor(analyzer, ui=ui, collector=collector, max_steps=3)

    # Step 1：进入子页面并滚动，但下一次观察到的页面仍完全相同。
    executor.run_step(
        name="滚动到边界后返回", action=lambda: (_ for _ in ()).throw(RuntimeError("target missing")),
        verify=lambda: ui.back_calls == 1, back_retries_action=False,
    )

    # Step 2：确认上下文标记滚动边界与分支耗尽。
    navigation = analyzer.calls[2]["context"]["navigation"]
    assert navigation["scroll_at_boundary"] is True
    assert navigation["branch_exhausted"] is True
    assert navigation["navigation_stack"][-1]["target"] == "我的设备"


def test_same_label_is_blocked_only_under_its_original_parent():
    """验证不同父页可分别进入“更多”，同一父页返回后不可重入。"""
    analyzer = FakeAnalyzer(
        _analysis(RecoveryAction.NAVIGATE, target_text="更多"),
        _analysis(RecoveryAction.NAVIGATE, target_text="更多"),
        _analysis(RecoveryAction.BACK),
        _analysis(RecoveryAction.NAVIGATE, target_text="更多"),
    )
    ui = FakeUI()
    collector = FakeContextCollector(ui_texts=[
        ["Page A", "更多"], ["Page B", "更多"], ["Page C"], ["Page B", "更多"],
    ])
    executor = _executor(analyzer, ui=ui, collector=collector, max_steps=4)

    # Step 1：先进入两个不同父页的同名入口，再回到第二个父页尝试重入。
    with pytest.raises(AIRecoveryError, match="target already explored"):
        executor.run_step(
            name="同名入口边界", action=lambda: (_ for _ in ()).throw(RuntimeError("target missing")),
            verify=lambda: False, back_retries_action=False,
        )

    # Step 2：确认两条不同边已执行，重复的第三次点击被拒绝。
    assert ui.clicked_texts == ["更多", "更多"]
    assert analyzer.calls[3]["context"]["navigation"]["blocked_targets_on_current_page"] == ["更多"]


def test_content_description_is_navigation_candidate():
    """验证仅含无障碍描述的可点击节点可成为导航候选。"""
    analyzer = FakeAnalyzer(_analysis(RecoveryAction.NAVIGATE, target_text="系统设置"))
    ui = FakeUI()
    collector = FakeContextCollector(ui_elements=[[
        {"text": "", "content_desc": "系统设置", "clickable": True, "scrollable": False},
    ]])
    executor = _executor(analyzer, ui=ui, collector=collector)

    # Step 1：提供只有 content-desc 的现场并让 AI 选择该入口。
    executor.run_step(
        name="无障碍描述导航", action=lambda: (_ for _ in ()).throw(RuntimeError("target missing")),
        verify=lambda: ui.clicked_texts == ["系统设置"],
    )

    # Step 2：确认候选来自结构化元素，统一点击方法收到该标签。
    assert analyzer.calls[0]["context"]["available_navigation_candidates"] == ["系统设置"]
    assert ui.clicked_texts == ["系统设置"]


def test_navigation_fingerprint_ignores_focus_bounds_and_status_bar():
    """验证焦点、坐标和状态栏文字变化不改变页面指纹。"""
    def context(focused: bool, bounds: str, time: str) -> dict[str, object]:
        return {
            "current_activity": "com.android.settings/.Settings",
            "ui_elements": [
                {"text": "系统", "content_desc": "", "resource_id": "settings/system", "package": "com.android.settings",
                 "clickable": True, "scrollable": False, "focused": focused, "bounds": bounds},
                {"text": time, "package": "com.android.systemui", "resource_id": "status/time"},
            ],
        }

    # Step 1：构造业务元素相同但焦点、坐标和系统时间不同的现场。
    first = context(False, "[0,0][100,100]", "12:01")
    second = context(True, "[4,4][104,104]", "12:02")

    # Step 2：确认统一指纹函数仅受稳定的应用页面信息影响。
    assert build_state_fingerprint(first) == build_state_fingerprint(second)


def test_device_state_is_cached_across_context_collections(tmp_path):
    """验证连续采集只读取一次设备静态状态。"""
    class CountingTV(FakeTV):
        calls = 0

        def get_device_state(self) -> DeviceState:
            self.calls += 1
            return super().get_device_state()

    dump_path = tmp_path / "window.xml"
    dump_path.write_text('<hierarchy><node text="系统" clickable="true"/></hierarchy>', encoding="utf-8")
    tv = CountingTV()
    collector = FailureContextCollector(tv, FakeUI(dump_path=dump_path))

    # Step 1：连续三轮采集恢复上下文。
    contexts = [collector.collect(step_name="缓存", error=RuntimeError("missing"), attempt=index)
                for index in range(1, 4)]

    # Step 2：确认设备状态复用且每轮 UI 信息仍可用。
    assert tv.calls == 1
    assert all(context["device_state"]["serial"] == "fake-device" for context in contexts)
    assert all(context["ui_texts"] == ["系统"] for context in contexts)


def test_adb_evidence_failure_keeps_ui_navigation_available(tmp_path):
    """验证 ADB 状态读取失败时仍从 UI hierarchy 提供导航候选。"""
    class FailingTV:
        def current_activity(self) -> str:
            raise TimeoutError("activity timeout")

        def get_device_state(self) -> DeviceState:
            raise TimeoutError("device state timeout")

    dump_path = tmp_path / "window.xml"
    dump_path.write_text('<hierarchy><node text="系统" clickable="true"/></hierarchy>', encoding="utf-8")
    ui = FakeUI(dump_path=dump_path, screenshot_error=TimeoutError("screenshot timeout"))
    collector = FailureContextCollector(FailingTV(), ui)
    analyzer = FakeAnalyzer(_analysis(RecoveryAction.NAVIGATE, target_text="系统"))
    executor = _executor(analyzer, ui=ui, collector=collector)

    # Step 1：让 Activity、设备状态和截图均超时，保留 UI hierarchy。
    executor.run_step(
        name="ADB 超时继续导航", action=lambda: (_ for _ in ()).throw(RuntimeError("target missing")),
        verify=lambda: ui.clicked_texts == ["系统"],
    )

    # Step 2：确认 AI 收到可点击候选并完成导航。
    context = analyzer.calls[0]["context"]
    assert context["available_navigation_candidates"] == ["系统"]
    assert context["ui_texts"] == ["系统"]
    assert "TimeoutError" in context["current_activity_error"]
    assert "TimeoutError" in context["device_state_error"]
    assert "TimeoutError" in context["screenshot_error"]


def test_context_keeps_adb_fallback_hierarchy_and_backend(tmp_path):
    """验证 ADB 降级成功后的 hierarchy 仍可作为 AI 导航证据。"""
    dump_path = tmp_path / "adb.xml"
    dump_path.write_text('<hierarchy><node text="系统" clickable="true"/></hierarchy>', encoding="utf-8")
    recovery = {"error_type": "RemoteDisconnected", "reconnected": True,
                "u2_retry_used": True, "u2_retry_success": False,
                "adb_fallback_used": True, "adb_fallback_success": True}
    ui = SimpleNamespace(
        dump_ui=lambda: dump_path, last_dump_backend="adb", last_transport_recovery=recovery,
        take_screenshot=lambda: tmp_path / "screenshot.png",
    )
    collector = FailureContextCollector(FakeTV(), ui)

    # Step 1：读取已由驱动降级保存的 ADB hierarchy。
    context = collector.collect(step_name="ADB 降级", error=RuntimeError("missing"), attempt=1,
                                include_screenshot=False)

    # Step 2：确认来源、恢复记录与可见元素均进入上下文。
    assert context["ui_dump_path"] == str(dump_path)
    assert context["ui_dump_backend"] == "adb"
    assert context["transport_recovery"] == recovery
    assert context["ui_texts"] == ["系统"]
    assert context["ui_elements"][0]["clickable"] is True
    assert context["screenshot_path"] is None


def test_transport_recovery_trace_does_not_consume_ai_step(tmp_path):
    """验证驱动层恢复只写追踪事件，不额外消耗 AI 步数。"""
    analyzer = FakeAnalyzer(_analysis(RecoveryAction.RETRY))
    collector = FakeContextCollector(ui_texts=[["系统"]])
    original_collect = collector.collect

    def collect(**kwargs: object) -> dict[str, object]:
        context = original_collect(**kwargs)
        context["ui_dump_backend"] = "adb"
        context["transport_recovery"] = {
            "error_type": "RemoteDisconnected", "reconnected": True,
            "u2_retry_success": False, "adb_fallback_used": True, "adb_fallback_success": True,
        }
        return context

    collector.collect = collect
    log_path = tmp_path / "transport.jsonl"
    executor = _executor(analyzer, collector=collector, trace_path=log_path)
    calls = 0

    def action() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("missing")
        return "done"

    # Step 1：模拟一次底层 ADB 降级后由 AI 正常重试业务动作。
    assert executor.run_step(name="通信恢复", action=action) == "done"

    # Step 2：确认通信恢复单独记日志，AI 仅决策一次。
    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    transport = next(event for event in events if event["event"] == "transport_recovery")
    assert transport["uiautomator_reconnect"] is True
    assert transport["adb_fallback_success"] is True
    assert sum(event["event"] == "ai_decision" for event in events) == 1
    assert next(event for event in events if event["event"] == "step_completed")["recovery_attempts"] == 1


def test_exhausted_transport_stops_before_ai_analysis(tmp_path):
    """验证所有 hierarchy 后端失败时直接报告设备通信故障。"""
    analyzer = FakeAnalyzer()
    collector = FakeContextCollector()

    def collect(**_kwargs: object) -> dict[str, object]:
        return {
            "current_activity": "com.android.settings/.SubSettings", "ui_elements": [], "ui_texts": [],
            "transport_unavailable": True,
            "ui_dump_error": "DeviceTransportError: transport unavailable",
            "transport_recovery": {
                "error_type": "RemoteDisconnected", "reconnected": True,
                "u2_retry_success": False, "adb_fallback_used": True, "adb_fallback_success": False,
            },
        }

    collector.collect = collect
    log_path = tmp_path / "transport_failed.jsonl"
    executor = _executor(analyzer, collector=collector, trace_path=log_path)

    # Step 1：模拟驱动重连和 ADB 降级均未取得 hierarchy。
    with pytest.raises(DeviceTransportError, match="Device transport unavailable"):
        executor.run_step(name="通信全部失败", action=lambda: (_ for _ in ()).throw(RuntimeError("missing")))

    # Step 2：确认 AI 未被调用，日志将根因标为设备通信。
    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert analyzer.calls == []
    assert any(event["event"] == "transport_recovery" for event in events)
    assert any(event["event"] == "step_failed" and event["failure_category"] == "device_transport"
               for event in events)


def test_initial_transport_error_bypasses_ai_recovery():
    """验证测试动作自身的设备通信故障也不会交给 AI 分析。"""
    analyzer = FakeAnalyzer()
    collector = FakeContextCollector()
    executor = _executor(analyzer, collector=collector)

    # Step 1：让初始设备动作直接报告通信故障。
    with pytest.raises(DeviceTransportError, match="transport unavailable"):
        executor.run_step(
            name="点击通信故障",
            action=lambda: (_ for _ in ()).throw(DeviceTransportError("Device UI transport unavailable")),
        )

    # Step 2：确认没有采集业务失败现场，也没有调用 AI。
    assert collector.calls == []
    assert analyzer.calls == []


def test_intermediate_recovery_skips_screenshot(tmp_path):
    """验证首次失败及最终失败截图，中间恢复轮次不截图。"""
    dump_path = tmp_path / "window.xml"
    dump_path.write_text('<hierarchy><node text="系统" clickable="true"/></hierarchy>', encoding="utf-8")
    ui = FakeUI(dump_path=dump_path)
    collector = FailureContextCollector(FakeTV(), ui)
    analyzer = FakeAnalyzer(_analysis(RecoveryAction.BACK), _analysis(RecoveryAction.HUMAN_INTERVENTION))
    executor = _executor(analyzer, ui=ui, collector=collector, max_steps=2)

    # Step 1：首次失败后返回一次，再进入最终人工介入状态。
    with pytest.raises(AIRecoveryError):
        executor.run_step(
            name="截图策略", action=lambda: (_ for _ in ()).throw(RuntimeError("missing")),
            verify=lambda: False, back_retries_action=False,
        )

    # Step 2：确认中间一轮无截图，最终失败另存截图证据。
    assert analyzer.calls[0]["context"]["screenshot_path"] is not None
    assert analyzer.calls[1]["context"]["screenshot_path"] is None
    assert ui.screenshot_calls == 2


def test_low_confidence_navigation_does_not_click():
    """验证点击建议低于独立门槛时不会操作设备。"""
    analyzer = FakeAnalyzer(_analysis(RecoveryAction.NAVIGATE, confidence=0.6, target_text="系统"))
    ui = FakeUI()
    executor = _executor(analyzer, ui=ui, collector=FakeContextCollector(ui_texts=[["系统"]]))

    # Step 1：让 AI 给出可见但置信度不足的点击建议。
    with pytest.raises(AIRecoveryError, match="threshold 0.70"):
        executor.run_step(
            name="低置信度点击",
            action=lambda: (_ for _ in ()).throw(RuntimeError("target absent")),
        )

    # Step 2：确认未执行真实点击。
    assert ui.clicked_texts == []


def test_context_uses_visible_elements_without_sending_full_xml(tmp_path):
    """验证完整 XML 留作证据且模型只接收可见节点。"""
    dump_path = tmp_path / "window.xml"
    dump_path.write_text(
        '<hierarchy><node text="系统" resource-id="settings/system" clickable="true" '
        'scrollable="false" visible-to-user="true"/><node text="隐藏" '
        'visible-to-user="false"/></hierarchy>',
        encoding="utf-8",
    )
    collector = FailureContextCollector(FakeTV(), FakeUI(dump_path=dump_path))

    # Step 1：从本地 XML 采集恢复现场。
    context = collector.collect(step_name="settings", error=RuntimeError("missing"), attempt=1)

    # Step 2：确认模型上下文仅含可见元素，原始 XML 文件仍可查阅。
    assert context["ui_hierarchy"] is None
    assert context["ui_texts"] == ["系统"]
    assert context["ui_elements"][0]["resource_id"] == "settings/system"
    assert context["ui_elements"][0]["package"] == ""
    assert context["ui_dump_path"] == str(dump_path)


def test_executor_writes_utf8_decision_and_action_trace(tmp_path):
    """验证恢复日志包含中文理由、决策字段和实际执行动作。"""
    log_path = tmp_path / "ai_recovery.jsonl"
    analyzer = FakeAnalyzer(_analysis(RecoveryAction.RETRY))
    executor = _executor(analyzer, trace_path=log_path)

    # Step 1：触发一次 AI 重试，并读取追加生成的 UTF-8 审计事件。
    calls = 0

    def action() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("首次点击失败")
        return "完成"

    assert executor.run_step(name="中文路径日志", action=action) == "完成"
    entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    events = [entry["event"] for entry in entries]
    decision = next(entry for entry in entries if entry["event"] == "ai_decision")

    assert "ai_decision" in events
    assert "step_completed" in events
    assert decision["reason"] == "Fake evidence based decision"
    assert decision["decision_steps"] == ["现场显示目标缺失", "当前动作可以安全重试"]
    assert decision["analysis_duration_ms"] >= 0
    assert decision["suggested_action"] == "retry"
    assert any(entry.get("action") == "retry" for entry in entries if entry["event"] == "action_started")
    assert entries[0]["step"] == "中文路径日志"


def test_default_device_tools_do_not_expose_adb_shell():
    """验证默认设备工具只注册白名单操作且不包含任意 ADB Shell。"""
    fake_tv = SimpleNamespace(
        remote=object(),
        ui=FakeUI(),
        current_activity=lambda: "com.android.settings/.Settings",
        get_device_state=lambda: DeviceState(serial="fake-device"),
    )

    # Step 1：创建默认设备工具集并核对注册名称。
    names = {tool["function"]["name"] for tool in build_device_tools(fake_tv).definitions()}

    assert names == {
        "get_current_activity",
        "get_device_state",
        "find_element",
        "exists",
        "dump_ui",
        "back",
        "home",
        "click",
        "click_text",
        "press_remote_key",
    }
