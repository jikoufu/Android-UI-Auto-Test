"""Verify the Acceptance handoff in disposable Git worktrees."""

import json
from pathlib import Path

import pytest

from agent_harness.workflows import dev_workflow as orchestrator
from agent_harness.workflows.acceptance_document import review_acceptance
from agent_harness.workflows.pytest_evidence import classify
from agent_harness.workflows.requirements_document import RequirementsError
from agent_harness.workflows.test_design_document import Plan, PlanCase
from unit_tests.engineering.test_development_workflow import approve_test_design, development_case
from unit_tests.engineering.test_git_workspace_skill import (
    dev_workflow, git, repositories, stage_args, workflow, workflow_report,
)
from unit_tests.engineering.test_test_design_workflow import configure_offline_python, offline_python


def write_report(task, *, conclusion="PASS", trace="PASS", tests="None", not_run="None", risks="None",
                 trace_evidence="fixture evidence", code_review="Reviewed sample.py."):
    path = Path(task["acceptance_path"])
    keys = ("task_id", "requirements_sha256", "design_sha256", "test_design_sha256", "development_sha256")
    metadata = "\n".join(f"- {key}: {task[key]}" for key in keys)
    path.write_text(
        f"## Version\n{metadata}\n- independent_reviewer: Acceptance fixture\n"
        "## Upstream\nApproved fixture documents and archive.\n"
        f"## Trace\n- FR-001 / AC-001 / TC-001: {trace} | {trace_evidence}\n"
        f"## Tests\n{tests}\n## Not Run\n{not_run}\n## Code Review\n{code_review}\n"
        f"## Risks\n{risks}\n## Conclusion\n{conclusion}\n", encoding="utf-8",
    )
    return path


@pytest.fixture
def approved_delivery(development_case):
    script, source, task, _ = development_case
    task_id = task["task_id"]
    denied = workflow(script, source, "acceptance-input", task_id)
    assert denied.returncode == 1
    approved_tests = approve_test_design(script, source, task)
    started = workflow_report(script, source, "continue", task_id, *stage_args(approved_tests))
    tree = Path(started["worktree_path"])
    (tree / "sample.py").write_text('def status():\n    return "ready"\n', encoding="utf-8")
    Path(started["development_path"]).write_text(
        "## Summary\nReturn the ready status.\n## Trace\n- FR-001 / AC-001: sample.status by TC-001.\n"
        "## Verification\nApproved offline test.\n", encoding="utf-8",
    )
    green = workflow_report(script, source, "test-run", task_id, *stage_args(started))
    pending = workflow_report(script, source, "submit", task_id, *stage_args(green))
    denied = workflow(script, source, "continue", task_id, *stage_args({**pending, "current_stage": "acceptance"}))
    assert denied.returncode == 1 and "Stale continue" in denied.stderr
    assert workflow(script, source, "acceptance-input", task_id).returncode == 1
    approved = workflow_report(script, source, "approve", task_id, pending["development_sha256"], *stage_args(pending))
    return script, source, approved


def test_acceptance_upstream_must_match_and_worktree_must_be_bound(approved_delivery):
    script, source, approved = approved_delivery
    task_id = approved["task_id"]
    tree = Path(approved["worktree_path"])
    for key in ("requirements_snapshot", "design_snapshot", "test_design_archive", "development_archive"):
        snapshot = Path(approved[key])
        original = snapshot.read_bytes()
        snapshot.write_bytes(original + b"\n")
        denied = workflow(script, source, "continue", task_id, *stage_args(approved))
        assert denied.returncode == 1 and not denied.stdout and "SHA-256" in denied.stderr
        snapshot.write_bytes(original)
    git(tree, "checkout", "--detach")
    denied = workflow(script, source, "continue", task_id, *stage_args(approved))
    assert denied.returncode == 1 and "registered linked Worktree" in denied.stderr
    git(tree, "checkout", approved["branch"])
    assert workflow_report(script, source, "status", task_id) == approved


def test_acceptance_pass_requires_new_evidence_and_current_human_approval(approved_delivery):
    script, source, approved = approved_delivery
    task_id = approved["task_id"]
    tree = Path(approved["worktree_path"])
    running = workflow_report(script, source, "continue", task_id, *stage_args(approved))
    context = workflow_report(script, source, "acceptance-input", task_id)
    assert all(context[name] for name in ("approved_requirements", "approved_design", "approved_test_plan"))
    assert context["worktree_path"] == approved["worktree_path"]
    report = write_report(running)
    assert report.is_relative_to(tree) and not (source / "docs/changes" / task_id).exists()
    assert workflow(script, source, "submit", task_id, *stage_args(running)).returncode == 1

    paused = workflow_report(script, source, "pause", task_id, *stage_args(running))
    assert report.is_file() and paused["paused_from"] == "running"
    assert workflow(script, source, "test-run", task_id, *stage_args(running)).returncode == 1
    resumed = workflow_report(script, source, "resume", task_id, *stage_args(paused))
    checked = workflow_report(script, source, "test-run", task_id, *stage_args(resumed))
    evidence = checked["acceptance_evidence"]
    assert evidence["exit_code"] == 0 and evidence["outcomes"]["TC-001"]["status"] == "passed"
    assert evidence["development_archive_sha256"] == approved["development_archive_sha256"]
    original = report.read_bytes()
    report.write_bytes(original.replace(b"TC-001", b"TC-002"))
    missing = workflow(script, source, "submit", task_id, *stage_args(checked))
    assert missing.returncode == 1 and "cover all and only" in missing.stderr
    write_report(checked, tests="- FAIL | 1 | pytest integration")
    assert "failed test run" in workflow(script, source, "submit", task_id, *stage_args(checked)).stderr
    write_report(checked, not_run="Device test was skipped.")
    assert "nonblocking omissions" in workflow(script, source, "submit", task_id, *stage_args(checked)).stderr
    write_report(checked, conclusion="PASS WITH RISKS", not_run="Supplemental device test was skipped.",
                 risks="Approved offline behavior verified; supplemental device comparison pending.")
    pending = workflow_report(script, source, "submit", task_id, *stage_args(checked))
    assert pending["status"] == "waiting_for_approval" and not pending["acceptance_snapshot"]
    paused = workflow_report(script, source, "pause", task_id, *stage_args(pending))
    resumed = workflow_report(script, source, "resume", task_id, *stage_args(paused))
    digest = pending["acceptance_sha256"]
    stale = workflow(script, source, "approve", task_id, digest, *stage_args(pending))
    assert stale.returncode == 1 and "Stale approve" in stale.stderr
    submitted = report.read_bytes()
    report.write_bytes(submitted + b"\n")
    assert workflow(script, source, "approve", task_id, digest, *stage_args(resumed)).returncode == 1
    report.write_bytes(submitted)
    shipped = workflow_report(script, source, "approve", task_id, digest, *stage_args(resumed))
    assert (shipped["current_stage"], shipped["status"]) == ("acceptance", "awaiting_integration")
    assert shipped["stages"]["acceptance"] == "completed"
    snapshot = Path(shipped["acceptance_snapshot"])
    assert snapshot.read_bytes() == submitted
    assert workflow_report(script, source, "status", task_id) == shipped
    snapshot.write_bytes(submitted + b"\n")
    assert "approved digest" in workflow(script, source, "status", task_id).stderr
    snapshot.write_bytes(submitted)

    module = tree / "sample.py"
    approved_source = module.read_bytes()
    git(tree, "add", "sample.py", "unit_tests/test_design/test_status.py")
    git(tree, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "Unmerged change")
    head = git(tree, "rev-parse", "HEAD")
    assert git(tree, "rev-list", "origin/master..HEAD").splitlines()[0] == head
    assert "separate manual review" in workflow(script, source, "continue", task_id, *stage_args(shipped)).stderr
    assert tree.is_dir() and git(tree, "branch", "--show-current") == shipped["branch"]
    other = workflow_report(script, source, "start", "unrelated-acceptance")
    assert other["worktree_path"] != shipped["worktree_path"]
    assert not (Path(other["worktree_path"]) / "docs/changes" / task_id).exists()
    assert workflow_report(script, source, "status", task_id) == shipped
    module.write_bytes(approved_source + b"\n# Requires review again.\n")
    assert "revise-development" in workflow(script, source, "status", task_id).stderr
    stale = workflow(script, source, "revise-development", task_id, *stage_args(resumed))
    assert stale.returncode == 1 and "Stale revise-development" in stale.stderr
    reopened = workflow_report(script, source, "revise-development", task_id, *stage_args(shipped))
    assert (reopened["current_stage"], reopened["status"]) == ("development", "running")
    assert reopened["acceptance_approval_id"] is None
    retired = reopened["approval_history"][-1]
    assert retired["sha256"] == digest
    assert retired["evidence"] == evidence and snapshot.read_bytes() == submitted
    assert module.read_bytes() != approved_source and tree.is_dir()


@pytest.mark.parametrize("mode", ["manual", "environment"])
def test_acceptance_requires_manual_observation_and_review(tmp_path, mode):
    keys = ("task_id", "requirements_sha256", "design_sha256", "test_design_sha256", "development_sha256")
    metadata = dict.fromkeys(keys, "fixture")
    task = {**metadata, "acceptance_path": tmp_path / "acceptance.md"}
    plan = Plan({"TC-001": PlanCase(mode, "Fixture environment", "Returns ready")}, (("FR-001", "AC-001", "TC-001"),))
    report = write_report(task, trace_evidence="N/A")
    with pytest.raises(RequirementsError, match="requires actual manual/environment evidence"):
        review_acceptance(report.read_bytes(), metadata, plan, None)
    observation = "2026-11-14 | verification log 42 | The display showed ready."
    report = write_report(task, trace_evidence=observation, code_review="N/A")
    with pytest.raises(RequirementsError, match="requires actual code review"):
        review_acceptance(report.read_bytes(), metadata, plan, None)
    report = write_report(task, trace_evidence=observation)
    assert review_acceptance(report.read_bytes(), metadata, plan, None) == "PASS"


def test_acceptance_blocked_and_failed_keep_bound_worktree(approved_delivery, monkeypatch):
    script, source, approved = approved_delivery
    task_id = approved["task_id"]
    tree = Path(approved["worktree_path"])
    running = workflow_report(script, source, "continue", task_id, *stage_args(approved))
    write_report(running, conclusion="BLOCKED", trace="NOT RUN")
    blocked = workflow_report(script, source, "submit", task_id, *stage_args(running))
    assert blocked["status"] == "waiting_for_input"
    assert workflow(script, source, "continue", task_id, *stage_args(blocked)).returncode == 1
    running = workflow_report(script, source, "continue", task_id, "--with-input", *stage_args(blocked))
    monkeypatch.setattr(orchestrator, "PROJECT_ROOT", source)
    root = orchestrator.state_root()
    # Model a failed independent test on the same previously approved implementation.
    monkeypatch.setattr(orchestrator, "run_captured_cases", lambda inputs, cases: {
        "policy": orchestrator.EVIDENCE_POLICY, "exit_code": 1, "outcomes": {"TC-001": {"status": "error"}},
    })
    failed = orchestrator.task_command(
        root, "test-run", task_id, expected_stage="acceptance", expected_version=running["state_version"],
    ).report()
    assert failed["status"] == "failed" and failed["acceptance_evidence"]["exit_code"] == 1
    write_report(failed)
    assert "match independent pytest evidence" in workflow(script, source, "submit", task_id, *stage_args(failed)).stderr
    report = write_report(failed, conclusion="FAIL", trace="FAIL")
    rejected = workflow(script, source, "submit", task_id, *stage_args(failed))
    assert rejected.returncode == 1
    result = json.loads(rejected.stdout)
    assert result["status"] == "failed" and result["acceptance_sha256"] is None
    assert result["acceptance_evidence"] == failed["acceptance_evidence"]
    assert report.is_file() and tree.is_dir()

    running = workflow_report(script, source, "continue", task_id, *stage_args(result))
    node = "unit_tests/test_design/test_status.py::test_status"
    skipped = classify({
        "exit_code": 0, "collection_error": None, "process_attempted": False,
        "reports": {node: [{"phase": "setup", "outcome": "skipped", "xfail": False}]},
    }, node, PlanCase("pass", node, "Returns ready"))
    assert skipped["status"] == "not_verified"
    monkeypatch.setattr(orchestrator, "run_captured_cases", lambda inputs, cases: {
        "policy": orchestrator.EVIDENCE_POLICY, "exit_code": 0, "outcomes": {"TC-001": skipped},
    })
    failed = orchestrator.task_command(
        root, "test-run", task_id, expected_stage="acceptance", expected_version=running["state_version"],
    ).report()
    assert failed["status"] == "failed"
    write_report(failed)
    assert "NOT RUN" in workflow(script, source, "submit", task_id, *stage_args(failed)).stderr
    write_report(failed, conclusion="BLOCKED", trace="NOT RUN", not_run="pytest skipped TC-001.")
    assert workflow_report(script, source, "submit", task_id, *stage_args(failed))["status"] == "waiting_for_input"
