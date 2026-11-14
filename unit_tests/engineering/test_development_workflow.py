"""Exercise Development in disposable Git worktrees."""

import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from agent_harness.workflows import dev_workflow as orchestrator
from unit_tests.engineering.test_git_workspace_skill import (
    dev_workflow, git, repositories, stage_args, workflow, workflow_report,
)
from unit_tests.engineering.test_test_design_workflow import (
    configure_offline_python, offline_python, start, write_plan,
)


@pytest.fixture
def development_case(repositories, dev_workflow):
    _, upstream, source = repositories
    module = upstream / "sample.py"
    module.write_text('def status():\n    return "pending"\n', encoding="utf-8")
    git(upstream, "add", "sample.py")
    git(upstream, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "Sample")
    git(upstream, "push", "origin", "master")
    task = start(dev_workflow, source, "development-sample")
    write_plan(task)
    tree = Path(task["worktree_path"])
    test = tree / "unit_tests/test_design/test_status.py"
    test.parent.mkdir(parents=True)
    test.write_text('from sample import status\n\ndef test_status():\n    assert status() == "ready"\n', encoding="utf-8")
    return dev_workflow, source, task, test


def approve_test_design(script, source, task):
    task_id = task["task_id"]
    red = workflow_report(script, source, "test-run", task_id, *stage_args(task))
    evidence = red["test_evidence"]
    assert evidence["exit_code"] == 1 and evidence["outcomes"]["TC-001"]["status"] == "red"
    pending = workflow_report(script, source, "submit", task_id, *stage_args(red))
    return workflow_report(script, source, "approve", task_id, pending["test_design_sha256"], *stage_args(pending))


def test_development_red_green_refactor_and_approval(development_case):
    """验证真实 Red、失败保留、最小实现、重构复测和人工批准链路。"""
    # Step 1：未经测试审批，开发输入命令必须拒绝。
    script, source, task, test = development_case
    task_id = task["task_id"]
    assert workflow(script, source, "development-input", task_id).returncode == 1
    no_red = workflow(script, source, "submit", task_id, *stage_args(task))
    assert no_red.returncode == 1 and "Run test-run" in no_red.stderr
    approved = approve_test_design(script, source, task)
    running = workflow_report(script, source, "continue", task_id, *stage_args(approved))
    assert running["status"] == "running" and running["worktree_path"] == task["worktree_path"]
    # 旧开发记录可直接继续，过时的交接字段不产生额外门禁。
    state_path = source / "runtime/state/dev-workflow" / task_id / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update(development_baseline_sha256=None, development_handoff_sha256="0" * 64)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    context = workflow_report(script, source, "development-input", task_id)
    assert context["state_version"] == running["state_version"]
    assert context["approved_test_evidence"]["outcomes"]["TC-001"]["status"] == "red"
    assert context["approved_test_evidence_scope"] == "test_design_archive"
    assert all(context[key] for key in ("approved_requirements", "approved_design", "approved_test_plan"))
    print("Development sample: Red", context["approved_test_evidence"]["exit_code"])

    # Step 2：测试失败保留诊断，不能提交；暂停恢复保留绑定和证据。
    failed = workflow(script, source, "test-run", task_id, *stage_args(running))
    assert failed.returncode == 1
    failed = json.loads(failed.stdout)
    assert failed["status"] == "failed" and failed["development_evidence"]["exit_code"] == 1
    assert workflow(script, source, "submit", task_id, *stage_args(failed)).returncode == 1
    running = workflow_report(script, source, "continue", task_id, *stage_args(failed))
    paused = workflow_report(script, source, "pause", task_id, *stage_args(running))
    assert workflow(script, source, "development-input", task_id).returncode == 1
    resumed = workflow_report(script, source, "resume", task_id, *stage_args(paused))
    assert resumed["development_evidence"] == failed["development_evidence"]
    waiting = workflow_report(script, source, "block", task_id, "Confirm the status input.", *stage_args(resumed))
    assert waiting["status"] == "waiting_for_input" and waiting["error"]
    assert workflow(script, source, "continue", task_id, *stage_args(waiting)).returncode == 1
    resumed = workflow_report(script, source, "continue", task_id, "--with-input", *stage_args(waiting))

    # Step 3：在绑定 Worktree 实现并重构，旧 Green 必须失效。
    original_test = test.read_bytes()
    tree = Path(resumed["worktree_path"])
    module = tree / "sample.py"
    module.write_text('def status():\n    return "ready"\n', encoding="utf-8")
    green = workflow_report(script, source, "test-run", task_id, *stage_args(resumed))
    assert green["development_evidence"]["exit_code"] == 0
    assert green["development_evidence"]["scope"] == "implementation_snapshot"
    assert green["development_evidence"]["fingerprint"] != context["approved_test_evidence"]["fingerprint"]
    print("Development sample: Green", green["development_evidence"]["exit_code"])
    module.write_text('READY = "ready"\n\ndef status():\n    return READY\n', encoding="utf-8")
    record = Path(resumed["development_path"])
    record.write_text(
        "## Summary\nReturn the ready status.\n## Trace\n- FR-001 / AC-001: sample.status by TC-001.\n"
        "## Verification\nApproved offline test; no device tests.\n", encoding="utf-8",
    )
    stale = workflow(script, source, "submit", task_id, *stage_args(green))
    assert stale.returncode == 1 and "current implementation" in stale.stderr
    checked = workflow_report(script, source, "test-run", task_id, *stage_args(green))
    assert checked["development_evidence"]["exit_code"] == 0
    print("Development sample: Refactor", checked["development_evidence"]["exit_code"])
    assert test.read_bytes() == original_test
    assert not (source / "sample.py").exists()

    # Step 4：只有人工批准当前摘要才完成；过期操作不能影响下一阶段。
    pending = workflow_report(script, source, "submit", task_id, *stage_args(checked))
    assert pending["status"] == "waiting_for_approval"
    record.write_text(record.read_text(encoding="utf-8") + "\nEdited after submit.\n", encoding="utf-8")
    assert workflow(script, source, "approve", task_id, pending["development_sha256"], *stage_args(pending)).returncode == 1
    running = workflow_report(script, source, "continue", task_id, *stage_args(pending))
    pending = workflow_report(script, source, "submit", task_id, *stage_args(running))
    accepted = workflow_report(script, source, "approve", task_id, pending["development_sha256"], *stage_args(pending))
    assert (accepted["current_stage"], accepted["status"]) == ("acceptance", "not_implemented")
    assert accepted["stages"]["development"] == "completed"
    assert workflow(script, source, "submit", task_id, *stage_args(checked)).returncode == 1
    assert workflow_report(script, source, "status", task_id) == accepted

    # Step 5：策略升级后仍可查询历史批准，重开开发则要求新证据。
    policy = script.with_name("pytest_evidence.py")
    old_policy = accepted["test_evidence"]["policy"]
    policy.write_text(
        policy.read_text(encoding="utf-8").replace(old_policy, "offline-unit-assertion-future"),
        encoding="utf-8",
    )
    historical = workflow_report(script, source, "status", task_id)
    assert historical == accepted
    assert historical["test_evidence"]["policy"] == old_policy
    original = module.read_bytes()
    module.write_bytes(original + b"\n# changed after approval\n")
    denied = workflow(script, source, "status", task_id)
    assert denied.returncode == 1 and "differs from approved Development archive" in denied.stderr
    module.write_bytes(original)
    reopened = workflow(script, source, "revise-development", task_id, *stage_args(accepted))
    assert reopened.returncode == 1 and "obsolete execution policy" in reopened.stdout
    revised = json.loads(reopened.stdout)
    rerun = workflow(script, source, "continue", task_id, *stage_args(revised))
    assert rerun.returncode == 1 and "obsolete execution policy" in rerun.stdout


def test_development_rejects_changed_snapshots_tests_and_other_worktrees(development_case):
    """验证开发前置快照、测试文件和 Git 绑定失配时停止，并隔离另一任务。"""
    # Step 1：批准后破坏设计快照，开发不能继续。
    script, source, task, test = development_case
    approved = approve_test_design(script, source, task)
    task_id = task["task_id"]
    running = workflow_report(script, source, "continue", task_id, *stage_args(approved))
    snapshot = Path(running["design_snapshot"])
    original = snapshot.read_bytes()
    snapshot.write_bytes(original + b"\n")
    denied = workflow(script, source, "development-input", task_id)
    assert denied.returncode == 1 and "SHA-256 mismatch" in denied.stderr
    snapshot.write_bytes(original)
    # Step 2：测试改动要求重审，另一个任务的工作区不受影响。
    original = test.read_bytes()
    test.write_bytes(original + b"\n# changed\n")
    denied = workflow(script, source, "test-run", task_id, *stage_args(running))
    assert denied.returncode == 1 and "revise-tests" in denied.stderr
    other = start(script, source, "another-development")
    assert other["worktree_path"] != task["worktree_path"]
    assert not (Path(other["worktree_path"]) / "unit_tests/test_design/test_status.py").exists()
    test.write_bytes(original)
    tree = Path(task["worktree_path"])
    git(tree, "checkout", "--detach")
    assert workflow(script, source, "development-input", task_id).returncode == 1
    git(tree, "checkout", task["branch"])
    revised = workflow_report(script, source, "revise-tests", task_id, *stage_args(running))
    assert (revised["current_stage"], revised["status"]) == ("test_design", "running")
    assert revised["test_design_approval_id"] is None
    assert workflow(script, source, "continue", task_id, *stage_args(running)).returncode == 1
    for args in ((), stage_args(running)):
        denied = workflow(script, source, "revise-requirements", task_id, *args)
        assert denied.returncode != 0 and not denied.stdout
    assert workflow_report(script, source, "status", task_id) == revised


def test_red_and_first_development_entry_share_untracked_source_inventory(development_case):
    """验证 Red 捕获未跟踪源码，且首次进入开发前拒绝新增模块。"""
    script, source, task, _ = development_case
    tree = Path(task["worktree_path"])
    helper = tree / "helper.py"
    helper.write_text('VALUE = "pending"\n', encoding="utf-8")
    (tree / "sample.py").write_text(
        'from helper import VALUE\n\ndef status():\n    return VALUE\n', encoding="utf-8",
    )
    approved = approve_test_design(script, source, task)
    with ZipFile(approved["test_design_archive"]) as archive:
        assert archive.read("files/helper.py") == helper.read_bytes()
    (tree / "added.py").write_text('VALUE = "ready"\n', encoding="utf-8")
    denied = workflow(script, source, "continue", task["task_id"], *stage_args(approved))
    assert denied.returncode == 1
    rejected = json.loads(denied.stdout)
    assert (rejected["current_stage"], rejected["status"]) == ("test_design", "failed")
    assert "invalidated" in rejected["error"]
    retried = workflow_report(script, source, "continue", task["task_id"], *stage_args(rejected))
    assert (retried["current_stage"], retried["status"]) == ("test_design", "running")
    assert workflow(script, source, "development-input", task["task_id"]).returncode == 1
    assert workflow(script, source, "submit", task["task_id"], *stage_args(retried)).returncode == 1


def test_development_approval_archives_verified_bytes_during_late_edit(development_case, monkeypatch):
    """验证落盘前改稿不会替换批准版本，且 status 拒绝漂移的工作副本。"""
    script, source, task, _ = development_case
    task_id = task["task_id"]
    approved = approve_test_design(script, source, task)
    running = workflow_report(script, source, "continue", task_id, *stage_args(approved))
    module = Path(task["worktree_path"]) / "sample.py"
    implemented = b'def status():\n    return "ready"\n'
    module.write_bytes(implemented)
    green = workflow_report(script, source, "test-run", task_id, *stage_args(running))
    record = Path(green["development_path"])
    record.write_text(
        "## Summary\nReturn ready.\n## Trace\n- FR-001 / AC-001: sample.status by TC-001.\n"
        "## Verification\nApproved node passed.\n", encoding="utf-8",
    )
    pending = workflow_report(script, source, "submit", task_id, *stage_args(green))
    monkeypatch.setattr(orchestrator, "PROJECT_ROOT", source)
    state_path = orchestrator.task_state_path(orchestrator.state_root(), task_id)
    replace_path = Path.replace

    def edit_at_state_replace(path, destination):
        if destination == state_path:
            module.write_bytes(b'def status():\n    return "broken"\n')
        return replace_path(path, destination)

    with monkeypatch.context() as changes:
        changes.setattr(Path, "replace", edit_at_state_replace)
        accepted = orchestrator.task_command(
            orchestrator.state_root(), "approve", task_id, approval=pending["development_sha256"],
            expected_stage="development", expected_version=pending["state_version"],
        )
    assert accepted.report()["development_approval_scope"] == "verified_archive"
    archive_path = Path(accepted.report()["development_archive"])
    with ZipFile(archive_path) as archive:
        assert archive.read("files/sample.py") == implemented
        assert archive.read("development.md") == record.read_bytes()
        assert json.loads(archive.read("evidence.json"))["green"] == green["development_evidence"]
    denied = workflow(script, source, "status", task_id)
    assert denied.returncode == 1 and not denied.stdout and "differs from approved Development" in denied.stderr
    module.write_bytes(implemented)
    assert workflow_report(script, source, "status", task_id) == accepted.report()
    archive_path.write_bytes(archive_path.read_bytes() + b"corrupt")
    denied = workflow(script, source, "status", task_id)
    assert denied.returncode == 1 and "SHA-256 mismatch" in denied.stderr


def test_revise_development_requires_fresh_green_and_approval(development_case):
    """验证已批准实现可显式重开，保留历史归档并重新运行测试。"""
    script, source, task, _ = development_case
    task_id = task["task_id"]
    approved = approve_test_design(script, source, task)
    running = workflow_report(script, source, "continue", task_id, *stage_args(approved))
    module = Path(task["worktree_path"]) / "sample.py"
    module.write_text('def status():\n    return "ready"\n', encoding="utf-8")
    green = workflow_report(script, source, "test-run", task_id, *stage_args(running))
    Path(green["development_path"]).write_text(
        "## Summary\nReturn ready.\n## Trace\n- FR-001 / AC-001: sample.status by TC-001.\n"
        "## Verification\nApproved node passed.\n", encoding="utf-8",
    )
    pending = workflow_report(script, source, "submit", task_id, *stage_args(green))
    accepted = workflow_report(script, source, "approve", task_id, pending["development_sha256"], *stage_args(pending))
    old_archive = Path(accepted["development_archive"])
    old_bytes = old_archive.read_bytes()
    module.write_text('def status():\n    return str("ready")\n', encoding="utf-8")
    denied = workflow(script, source, "status", task_id)
    assert denied.returncode == 1 and "revise-development" in denied.stderr
    assert workflow(script, source, "revise-development", task_id, *stage_args(pending)).returncode != 0
    reopened = workflow_report(script, source, "revise-development", task_id, *stage_args(accepted))
    assert (reopened["current_stage"], reopened["status"]) == ("development", "running")
    assert reopened["development_evidence"] is None
    assert reopened["approval_history"][-1]["archive_sha256"] == accepted["development_archive_sha256"]
    assert old_archive.read_bytes() == old_bytes
    assert workflow(script, source, "submit", task_id, *stage_args(reopened)).returncode == 1
    green = workflow_report(script, source, "test-run", task_id, *stage_args(reopened))
    pending = workflow_report(script, source, "submit", task_id, *stage_args(green))
    accepted_again = workflow_report(script, source, "approve", task_id, pending["development_sha256"], *stage_args(pending))
    assert accepted_again["current_stage"] == "acceptance"
    assert accepted_again["development_archive"] != str(old_archive)
    assert workflow_report(script, source, "status", task_id) == accepted_again
