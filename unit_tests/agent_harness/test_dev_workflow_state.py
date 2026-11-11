"""Regression tests for task state persistence and validation."""

import json
from pathlib import Path
import sys
from uuid import UUID

import pytest

from agent_harness.workflows import dev_workflow


@pytest.fixture
def state(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("TV_AUTOMATION_RUNTIME_DIR", str(runtime))
    name = "task-" + "a" * 32
    return {
        "worktree_path": str(tmp_path / name),
        "branch": f"codex/git-workspace/{name}",
        "base_commit": "b" * 40,
    }


def test_final_state_write_failure_does_not_emit_success(state, monkeypatch, capsys):
    task_id = "c" * 32
    path = dev_workflow.state_root() / task_id / "state.json"
    replace_path = Path.replace

    def fail_final_replace(source, destination):
        if destination == path and path.exists():
            raise PermissionError("simulated state write failure")
        return replace_path(source, destination)

    monkeypatch.setattr(Path, "replace", fail_final_replace)
    monkeypatch.setattr(dev_workflow, "uuid4", lambda: UUID(task_id))
    monkeypatch.setattr(dev_workflow, "create_workspace", lambda slug: state)
    monkeypatch.setattr(dev_workflow, "task_document", lambda task, *, prepare: task.requirements_path())
    monkeypatch.setattr(sys, "argv", ["dev_workflow.py", "start", "task"])

    assert dev_workflow.main() == 1

    output = capsys.readouterr()
    assert not output.out
    assert "Could not persist the final state" in output.err
    for detail in (str(path), "simulated state write failure", *state.values()):
        assert detail in output.err
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["current_stage"] == "git_workspace" and saved["status"] == "running"
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("field", ["worktree_path", "branch", "base_commit", "current_stage", "status"])
def test_required_fields_cannot_be_omitted(state, monkeypatch, capsys, field):
    task_id = "c" * 32
    record = {**state, "task_id": task_id, "current_stage": "requirements", "status": "paused"}
    del record[field]
    assert_invalid_record(record, task_id, monkeypatch, capsys)


@pytest.mark.parametrize(("field", "value"), [
    ("worktree_path", None), ("worktree_path", "relative/path"),
    ("branch", "main"), ("branch", "codex/git-workspace/task-" + "d" * 32),
    ("base_commit", "abcd1234"), ("base_commit", "g" * 40),
])
def test_invalid_workspace_fields_block_task_operations(state, monkeypatch, capsys, field, value):
    task_id = "c" * 32
    record = {**state, "task_id": task_id, "current_stage": "requirements", "status": "paused", field: value}
    assert_invalid_record(record, task_id, monkeypatch, capsys, field)


def assert_invalid_record(record, task_id, monkeypatch, capsys, field=None):
    path = dev_workflow.state_root() / task_id / "state.json"
    path.parent.mkdir(parents=True)
    original = json.dumps(record)
    path.write_text(original, encoding="utf-8")
    for operation in ("status", "pause", "resume"):
        monkeypatch.setattr(sys, "argv", ["dev_workflow.py", operation, task_id])
        assert dev_workflow.main() == 1
        output = capsys.readouterr()
        assert not output.out
        assert "Invalid task record" in output.err
        if field is not None:
            assert field in output.err
    assert path.read_text(encoding="utf-8") == original


def test_codex_and_claude_use_the_same_internal_requirements_and_design_skills():
    root = dev_workflow.PROJECT_ROOT
    codex = (root / ".agents/skills/dev-workflow/SKILL.md").read_text(encoding="utf-8")
    claude = (root / ".claude/skills/dev-workflow/SKILL.md").read_text(encoding="utf-8")
    policy = (root / ".agents/skills/dev-workflow/agents/openai.yaml").read_text(encoding="utf-8")
    assert ".agents/skills/dev-workflow/SKILL.md" in claude
    assert "disable-model-invocation: true" in claude
    assert "allow_implicit_invocation: false" in policy

    workflow = root / "agent_harness/workflows/workflow.md"
    assert "agent_harness/workflows/workflow.md" in codex
    for internal in ("../skills/requirement_analysis/SKILL.md", "../skills/architecture_design/SKILL.md"):
        assert f"]({internal})" in workflow.read_text(encoding="utf-8")
        assert (workflow.parent / internal).is_file()
    for native_directory in (root / ".agents/skills", root / ".claude/skills"):
        assert not any((native_directory / name / "SKILL.md").exists() for name in (
            "requirements", "requirement-analysis", "requirement_analysis", "design", "architecture_design",
        ))
