"""Integration checks against temporary repositories and a local bare remote."""

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import os
from pathlib import Path
from shutil import copy2
import subprocess
import sys

import pytest

from agent_harness.workflows import dev_workflow as orchestrator
from agent_harness.workflows.approved_artifacts import publish_snapshot, read_snapshot, snapshot_path
from agent_harness.workflows.design_document import review_design
from agent_harness.workflows.requirements_document import RequirementsError, review_requirements


SCRIPT = Path(__file__).resolve().parents[2] / ".agents/skills/git-workspace/scripts/git_workspace.py"
WORKFLOW_SCRIPT = Path("agent_harness/workflows/dev_workflow.py")


@pytest.fixture
def repositories(tmp_path, monkeypatch):
    for key in os.environ:
        if key.startswith("GIT_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global-config"))
    monkeypatch.delenv("TV_AUTOMATION_BASE_BRANCH", raising=False)
    remote = tmp_path / "origin.git"
    upstream = tmp_path / "upstream"
    source = tmp_path / "working copy"
    git(tmp_path, "init", "--bare", "--initial-branch=master", str(remote))
    git(tmp_path, "init", "--initial-branch=master", str(upstream))
    (upstream / ".gitignore").write_text("*.local\n", encoding="utf-8")
    (upstream / "tracked.txt").write_text("base\n", encoding="utf-8")
    git(upstream, "add", ".")
    git(upstream, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "base")
    git(upstream, "remote", "add", "origin", str(remote))
    git(upstream, "push", "origin", "master")
    git(tmp_path, "clone", str(remote), str(source))
    return remote, upstream, source


def git(directory, *args):
    return subprocess.run(
        ["git", "-C", str(directory), *args], check=True, capture_output=True, text=True, timeout=30,
    ).stdout.strip()


def skill(directory, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)], cwd=directory, capture_output=True,
        text=True, encoding="utf-8", timeout=30,
    )


def test_create_fetches_fresh_master_and_keeps_existing_worktrees(repositories):
    _, upstream, source = repositories
    old_head = git(source, "rev-parse", "HEAD")
    existing = source.with_name("existing task")
    git(source, "worktree", "add", "-b", "existing-task", str(existing))
    (existing / "tracked.txt").write_bytes(b"linked worktree edits\n")
    (source / "tracked.txt").write_bytes(b"staged edits\n")
    git(source, "add", "tracked.txt")
    (source / "tracked.txt").write_bytes(b"unstaged edits\n")
    (source / "untracked.txt").write_bytes(b"untracked work\n")
    original_status = git(source, "status", "--porcelain")

    # A narrowed fetch mapping must not leave origin/master stale or update other refs.
    git(source, "config", "remote.origin.fetch", "+refs/heads/master:refs/remotes/origin/other")
    git(source, "config", "remote.origin.prune", "true")
    git(source, "update-ref", "refs/remotes/origin/other", old_head)
    git(upstream, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "--allow-empty", "-m", "fresh remote commit")
    git(upstream, "push", "origin", "master")
    fresh_head = git(upstream, "rev-parse", "HEAD")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(skill, source, "create", "same-task")
        second = pool.submit(skill, existing, "create", "same-task")
        results = [first.result(), second.result()]

    assert all(result.returncode == 0 for result in results), [result.stderr for result in results]
    workspaces = [json.loads(result.stdout) for result in results]
    assert len({item["branch"] for item in workspaces}) == 2
    assert len({item["worktree_path"] for item in workspaces}) == 2
    for item in workspaces:
        path = Path(item["worktree_path"])
        assert path.is_absolute() and path.parent == source.with_name("working copy.worktrees")
        assert item["branch"] == f"codex/git-workspace/{path.name}"
        assert git(path, "branch", "--show-current") == item["branch"]
        assert git(path, "rev-parse", "HEAD") == item["base_commit"] == fresh_head
        assert git(path, "status", "--porcelain") == ""

    assert git(source, "rev-parse", "origin/master") == fresh_head
    assert git(source, "rev-parse", "origin/other") == old_head
    assert git(source, "rev-parse", "HEAD") == old_head
    assert git(source, "branch", "--show-current") == "master"
    assert git(source, "status", "--porcelain") == original_status
    assert git(source, "show", ":tracked.txt") == "staged edits"
    assert (source / "tracked.txt").read_bytes() == b"unstaged edits\n"
    assert (source / "untracked.txt").read_bytes() == b"untracked work\n"
    assert (existing / "tracked.txt").read_bytes() == b"linked worktree edits\n"


def test_missing_master_does_not_use_stale_ref_or_same_named_tag(repositories):
    remote, upstream, source = repositories
    git(upstream, "push", "origin", "master:main")
    git(upstream, "push", "origin", "master:refs/tags/master")
    git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
    git(upstream, "push", "origin", ":refs/heads/master")
    stale_head = git(source, "rev-parse", "origin/master")
    before = git(source, "worktree", "list", "--porcelain")

    result = skill(source, "create", "missing-master")

    assert result.returncode == 1 and "origin has no master branch" in result.stderr
    assert git(source, "worktree", "list", "--porcelain") == before
    assert git(source, "branch", "--list", "codex/git-workspace/*") == ""
    assert git(source, "rev-parse", "origin/master") == stale_head
    assert not source.with_name("working copy.worktrees").exists()


def test_missing_configured_baseline_does_not_use_master_or_stale_ref(repositories, monkeypatch):
    _, _, source = repositories
    branch = "release/next"
    tracking_ref = f"refs/remotes/origin/{branch}"
    master = git(source, "rev-parse", "origin/master")
    git(source, "update-ref", tracking_ref, master)
    monkeypatch.setenv("TV_AUTOMATION_BASE_BRANCH", branch)
    before = git(source, "worktree", "list", "--porcelain")

    result = skill(source, "create", "missing-baseline")

    assert result.returncode == 1 and not result.stdout
    assert f"origin has no {branch} branch" in result.stderr
    assert git(source, "rev-parse", tracking_ref) == master
    assert git(source, "worktree", "list", "--porcelain") == before
    assert not source.with_name("working copy.worktrees").exists()


@pytest.mark.parametrize("branch", ["HEAD", "main:master"])
def test_invalid_baseline_configuration_is_rejected(repositories, monkeypatch, branch):
    _, _, source = repositories
    monkeypatch.setenv("TV_AUTOMATION_BASE_BRANCH", branch)

    result = skill(source, "create", "invalid-baseline")

    assert result.returncode == 1 and not result.stdout
    assert "TV_AUTOMATION_BASE_BRANCH must be a Git branch name" in result.stderr
    assert not source.with_name("working copy.worktrees").exists()


def test_cleanup_only_previews_clean_registered_worktree(repositories):
    _, _, source = repositories
    created = skill(source, "create", "cleanup-task")
    assert created.returncode == 0, created.stderr
    task = json.loads(created.stdout)
    path = Path(task["worktree_path"])

    assert skill(source, "cleanup", source).returncode == 1
    git(source, "worktree", "lock", str(path))
    assert "locked" in skill(source, "cleanup", path).stderr
    git(source, "worktree", "unlock", str(path))
    for name in ("tracked.txt", "untracked.txt", "ignored.local"):
        changed = path / name
        changed.write_bytes(b"local edits\n")
        result = skill(source, "cleanup", path)
        assert result.returncode == 1 and "local changes" in result.stderr
        if name == "tracked.txt":
            git(path, "restore", "--", name)
        else:
            changed.unlink()
    git(path, "update-index", "--assume-unchanged", "tracked.txt")
    assert "assume-unchanged" in skill(source, "cleanup", path).stderr
    git(path, "update-index", "--no-assume-unchanged", "tracked.txt")

    result = skill(source, "cleanup", path)

    assert result.returncode == 0, result.stderr
    preview = json.loads(result.stdout)
    assert preview["remove_argv"] == ["git", "-C", str(source), "worktree", "remove", "--", str(path)]
    assert path.is_dir() and git(path, "branch", "--show-current") == task["branch"]


@pytest.fixture
def dev_workflow(repositories, monkeypatch):
    _, _, source = repositories
    project = Path(__file__).resolve().parents[2]
    for script in (
        WORKFLOW_SCRIPT, WORKFLOW_SCRIPT.with_name("requirements_document.py"),
        WORKFLOW_SCRIPT.with_name("approved_artifacts.py"), WORKFLOW_SCRIPT.with_name("design_document.py"),
        WORKFLOW_SCRIPT.with_name("test_design_document.py"), WORKFLOW_SCRIPT.with_name("pytest_evidence.py"),
        WORKFLOW_SCRIPT.with_name("pytest_runner.py"),
        SCRIPT.relative_to(project),
    ):
        destination = source / script
        destination.parent.mkdir(parents=True, exist_ok=True)
        copy2(project / script, destination)
    monkeypatch.setenv("TV_AUTOMATION_RUNTIME_DIR", "runtime")
    return source / WORKFLOW_SCRIPT


def workflow(script, directory, *args):
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(script), *args], cwd=directory, capture_output=True,
        text=True, encoding="utf-8", timeout=30,
    )


def test_dev_workflow_persists_workspace_and_pauses_without_recreating_it(repositories, dev_workflow):
    _, _, source = repositories
    created = workflow(dev_workflow, source.parent, "start", "fix-navigation")
    assert created.returncode == 0, created.stderr
    task = json.loads(created.stdout)
    task_id = task["task_id"]
    path = Path(task["worktree_path"])
    assert path.is_absolute()
    assert git(path, "branch", "--show-current") == task["branch"]
    assert git(path, "rev-parse", "HEAD") == task["base_commit"] == git(source, "rev-parse", "origin/master")
    assert task["current_stage"] == task["next_stage"] == "requirements"
    assert task["status"] == "running"
    assert task["stages"] == {
        "git_workspace": "completed", "requirements": "running", "design": "not_implemented",
        "test_design": "not_implemented", "development": "not_implemented", "acceptance": "not_implemented",
        "integration_cleanup": "not_implemented",
    }
    record = source / "runtime" / "state" / "dev-workflow" / task_id / "state.json"
    assert json.loads(record.read_text(encoding="utf-8"))["base_commit"] == task["base_commit"]

    # The workspace script and remote can be unavailable after start.
    (source / SCRIPT.relative_to(Path(__file__).resolve().parents[2])).unlink()
    git(source, "remote", "remove", "origin")
    before = git(source, "worktree", "list", "--porcelain")
    for operation, expected, version_offset in (
        ("status", "running", 0), ("pause", "paused", 1), ("pause", "paused", 1),
        ("status", "paused", 1), ("resume", "running", 2), ("resume", "running", 3),
    ):
        result = workflow(dev_workflow, path, operation, task_id)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {
            **task, "status": expected, "paused_from": "running" if expected == "paused" else None,
            "state_version": task["state_version"] + version_offset,
            "stages": {**task["stages"], "requirements": expected},
        }
        assert json.loads(record.read_text(encoding="utf-8"))["status"] == expected
    assert git(source, "worktree", "list", "--porcelain") == before


def test_dev_workflow_stops_on_workspace_failure(repositories, dev_workflow, monkeypatch):
    remote, upstream, source = repositories
    git(upstream, "push", "origin", "master:main")
    git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
    git(upstream, "push", "origin", ":refs/heads/master")
    override = source.parent / "custom runtime"
    monkeypatch.setenv("TV_AUTOMATION_RUNTIME_DIR", str(override))
    before = git(source, "worktree", "list", "--porcelain")

    result = workflow(dev_workflow, source, "start", "no-master")

    assert result.returncode == 1
    task = json.loads(result.stdout)
    task_id = task["task_id"]
    assert "origin has no master branch" in task["error"]
    assert task["current_stage"] == "git_workspace" and task["status"] == "failed"
    assert task["stages"]["git_workspace"] == "failed"
    assert task["stages"]["requirements"] == "not_implemented"
    assert all(task[key] is None for key in ("worktree_path", "branch", "base_commit", "next_stage"))
    assert (override / "state" / "dev-workflow" / task_id / "state.json").is_file()
    status = workflow(dev_workflow, source, "status", task_id)
    assert status.returncode == 0 and json.loads(status.stdout) == task
    assert workflow(dev_workflow, source, "pause", task_id).returncode == 1
    assert workflow(dev_workflow, source, "resume", task_id).returncode == 1
    assert workflow(dev_workflow, source, "status", "../state.json").returncode == 1
    assert git(source, "worktree", "list", "--porcelain") == before


def test_dev_workflow_starts_from_explicit_main(repositories, dev_workflow, monkeypatch):
    remote, upstream, source = repositories
    old_master = git(source, "rev-parse", "origin/master")
    git(upstream, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "--allow-empty", "-m", "main baseline")
    main = git(upstream, "rev-parse", "HEAD")
    git(upstream, "push", "origin", "HEAD:refs/heads/main")
    git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
    git(upstream, "push", "origin", ":refs/heads/master")
    monkeypatch.setenv("TV_AUTOMATION_BASE_BRANCH", "main")

    result = workflow(dev_workflow, source, "start", "explicit-main")

    assert result.returncode == 0, result.stderr
    task = json.loads(result.stdout)
    assert task["status"] == "running"
    assert task["base_commit"] == main == git(source, "rev-parse", "origin/main")
    assert git(Path(task["worktree_path"]), "rev-parse", "HEAD") == main
    assert git(source, "rev-parse", "origin/master") == old_master != main


def requirements_draft(questions):
    return f"""# 示例需求

## 需求背景与业务目标
操作员需要查看本地状态。
## 当前系统行为
`tracked.txt` 保存现有行为说明。
## 需求范围与非目标
仅显示本地状态，保留现有写入规则。
## 角色及业务场景
操作员读取当前状态。
## 功能需求
- FR-001 显示当前状态。
## 业务规则与约束
仅使用已有状态来源。
## 异常及边界场景
缺少状态时显示缺失原因。
## 验收标准
- AC-001 已有状态时，打开状态视图可看到对应状态。
## 待确认问题
{questions}
## 需求与验收标准追踪关系
- FR-001: AC-001
"""


@pytest.mark.parametrize(("section", "record"), [
    ("FR", "* FR-002 Untracked requirement"),
    ("FR", "1. **FR-002** Untracked requirement"),
    ("FR", "**FR-ABC** Malformed identifier"),
    ("FR", "- FR-000 Invalid identifier"),
    ("FR", "- FR-002"),
    ("FR", "- FR-001 Duplicate identifier"),
    ("FR", "```\n- FR-002 Hidden record\n```"),
    ("AC", "| AC-002 | Untracked criterion |"),
    ("AC", "- AC-001 Duplicate identifier"),
    ("trace", "- FR-001: AC-001, AC-001"),
    ("trace", "- FR-001: AC-999"),
    ("trace", "- FR-002: AC-001"),
    ("trace", ""),
])
def test_requirements_rejects_invalid_or_untracked_records(section, record):
    draft = requirements_draft("无")
    if section in ("FR", "AC"):
        draft = draft.replace(f"- {section}-001 ", f"{record}\n- {section}-001 ", 1)
    else:
        draft = draft.replace("- FR-001: AC-001", record)
    with pytest.raises(RequirementsError):
        review_requirements(draft.encode("utf-8"))


def test_requirements_approval_freezes_reviewed_bytes_during_concurrent_edit(
    repositories, dev_workflow, monkeypatch,
):
    _, _, source = repositories
    monkeypatch.setattr(orchestrator, "PROJECT_ROOT", source)
    root = orchestrator.state_root()
    task = orchestrator.start(root, "approval-race")
    assert task.status == "running", task.error
    draft = task.requirements_path()
    original = requirements_draft("无").encode("utf-8")
    draft.write_bytes(original)
    pending = orchestrator.task_command(
        root, "submit", task.task_id, expected_stage=task.current_stage, expected_version=task.state_version,
    )
    changed = original.replace(b"tracked.txt", b"other.txt")
    publish = orchestrator.publish_snapshot

    def edit_before_publish(worktree, path, data):
        draft.write_bytes(changed)
        publish(worktree, path, data)

    monkeypatch.setattr(orchestrator, "publish_snapshot", edit_before_publish)
    approved = orchestrator.task_command(
        root, "approve", task.task_id, approval=pending.requirements_sha256,
        expected_stage=pending.current_stage, expected_version=pending.state_version,
    )
    worktree = Path(approved.worktree_path)
    snapshot = snapshot_path(
        worktree, approved.task_id, "requirements", approved.requirements_approval_id,
    )
    assert read_snapshot(worktree, snapshot, approved.requirements_sha256) == original
    assert draft.read_bytes() == changed
    assert orchestrator.task_command(root, "status", task.task_id) == approved
    assert orchestrator.design_input(root, task.task_id)["approved_requirements"] == original.decode("utf-8")
    with pytest.raises(FileExistsError):
        publish_snapshot(worktree, snapshot, changed)
    assert snapshot.read_bytes() == original


def test_requirements_review_and_explicit_versioned_approval(repositories, dev_workflow):
    _, _, source = repositories
    started = workflow(dev_workflow, source, "start", "requirements-review")
    assert started.returncode == 0, started.stderr
    task = json.loads(started.stdout)
    task_id = task["task_id"]
    target = Path(task["requirements_path"])
    assert target == Path(task["worktree_path"]) / "docs" / "changes" / task_id / "requirements.md"
    assert target.parent.is_dir()
    before = git(source, "worktree", "list", "--porcelain")

    missing = workflow(dev_workflow, source, "submit", task_id, *stage_args(task))
    assert missing.returncode == 1 and "Requirements path is missing" in missing.stderr
    assert workflow(dev_workflow, source, "approve", task_id, "0" * 64, *stage_args(task)).returncode == 1

    # The model owns document content. The fixture stands in for the model's file write.
    target.write_text(requirements_draft("- Q-001 缺失时需要显示哪种原因？"), encoding="utf-8")
    pending = workflow(dev_workflow, source, "submit", task_id, *stage_args(task))
    assert pending.returncode == 0, pending.stderr
    assert json.loads(pending.stdout)["status"] == "waiting_for_input"
    assert workflow(dev_workflow, source, "approve", task_id, "0" * 64, *stage_args(json.loads(pending.stdout))).returncode == 1

    paused = workflow(dev_workflow, source, "pause", task_id)
    assert json.loads(paused.stdout)["paused_from"] == "waiting_for_input"
    previous = target.read_bytes()
    assert workflow(dev_workflow, source, "continue", task_id).returncode == 1
    restored = workflow(dev_workflow, source, "resume", task_id)
    assert json.loads(restored.stdout)["status"] == "waiting_for_input"
    assert target.read_bytes() == previous

    assert workflow(dev_workflow, source, "continue", task_id).returncode == 1
    continued = workflow(dev_workflow, source, "continue", task_id, "--with-input")
    running = json.loads(continued.stdout)
    assert running["status"] == "running"
    target.write_text(requirements_draft("无").replace("- FR-001: AC-001", "- FR-001: AC-999"), encoding="utf-8")
    invalid = workflow(dev_workflow, source, "submit", task_id, *stage_args(running))
    assert invalid.returncode == 1 and "trace section" in invalid.stderr
    target.write_text(requirements_draft("无"), encoding="utf-8")
    submitted = workflow(dev_workflow, source, "submit", task_id, *stage_args(running))
    assert submitted.returncode == 0, submitted.stderr
    reviewed = json.loads(submitted.stdout)
    digest = reviewed["requirements_sha256"]
    assert reviewed["current_stage"] == "requirements" and reviewed["status"] == "waiting_for_approval"
    assert workflow(dev_workflow, source, "approve", task_id).returncode != 0
    assert workflow(dev_workflow, source, "approve", task_id, "0" * 64, *stage_args(reviewed)).returncode == 1
    assert workflow(dev_workflow, source, "pause", task_id).returncode == 0
    resumed = workflow_report(dev_workflow, source, "resume", task_id)
    assert resumed == {**reviewed, "state_version": reviewed["state_version"] + 2}
    obsolete = workflow(dev_workflow, source, "approve", task_id, digest, *stage_args(reviewed))
    assert obsolete.returncode == 1 and "Stale approve" in obsolete.stderr

    original = target.read_bytes()
    target.write_bytes(original + b"\n")
    stale = workflow(dev_workflow, source, "approve", task_id, digest, *stage_args(resumed))
    assert stale.returncode == 1 and "changed after submission" in stale.stderr
    assert json.loads(workflow(dev_workflow, source, "status", task_id).stdout) == resumed
    target.write_bytes(original)
    approved = workflow(dev_workflow, source, "approve", task_id, digest, *stage_args(resumed))
    assert approved.returncode == 0, approved.stderr
    finished = json.loads(approved.stdout)
    assert finished["current_stage"] == "design" and finished["status"] == "running"
    assert finished["stages"]["requirements"] == "completed"
    assert finished["requirements_completed"] is True
    assert finished["requirements_sha256"] == digest
    assert git(source, "worktree", "list", "--porcelain") == before
    assert not (source / "docs" / "changes").exists()


def test_requirements_documents_are_isolated_and_missing_worktree_blocks_analysis(repositories, dev_workflow):
    _, _, source = repositories
    first = workflow(dev_workflow, source, "start", "same-task")
    second = workflow(dev_workflow, source, "start", "same-task")
    assert first.returncode == second.returncode == 0, (first.stderr, second.stderr)
    a, b = map(json.loads, (first.stdout, second.stdout))
    first_doc, second_doc = map(Path, (a["requirements_path"], b["requirements_path"]))
    assert first_doc != second_doc and a["worktree_path"] != b["worktree_path"]
    first_doc.write_text(requirements_draft("无"), encoding="utf-8")
    assert not second_doc.exists()
    second_doc.write_text(requirements_draft("- Q-001 第二个任务仍有问题。"), encoding="utf-8")
    assert workflow_report(dev_workflow, source, "submit", a["task_id"], *stage_args(a))["status"] == "waiting_for_approval"
    assert workflow_report(dev_workflow, source, "submit", b["task_id"], *stage_args(b))["status"] == "waiting_for_input"
    assert "第二个任务" not in first_doc.read_text(encoding="utf-8")

    second_doc.unlink()
    git(source, "worktree", "remove", b["worktree_path"])
    rejected = workflow(dev_workflow, source, "continue", b["task_id"], "--with-input")
    assert rejected.returncode == 1, rejected.stderr
    unavailable = json.loads(rejected.stdout)
    assert unavailable["current_stage"] == "requirements" and unavailable["status"] == "failed"
    assert "Worktree is missing" in unavailable["error"]
    assert not Path(b["worktree_path"]).exists()
    assert first_doc.is_file()
    assert not (source / "docs" / "changes").exists()


def workflow_report(script, source, *args):
    result = workflow(script, source, *args)
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


def stage_args(task):
    return ("--stage", task["current_stage"], "--version", str(task["state_version"]))


def approve_requirements(script, source, task):
    Path(task["requirements_path"]).write_text(requirements_draft("无"), encoding="utf-8")
    submitted = workflow_report(script, source, "submit", task["task_id"], *stage_args(task))
    return workflow_report(script, source, "approve", task["task_id"], submitted["requirements_sha256"], *stage_args(submitted))


def design_draft(task, *, questions="无", trace="- FR-001: status | reader | 读取当前状态", version=1):
    return f"""# 技术设计
## 文档元数据
- task_id: {task["task_id"]}
- requirements_sha256: {task["requirements_sha256"]}
- requirements_approval_id: {task["requirements_approval_id"]}
- design_version: {version}
## 设计目标与非目标
显示本地状态，保留现有写入规则。
## 当前系统架构及相关实现
`tracked.txt` 存放现有状态说明。
## 受影响模块与变更范围
仅增加状态展示边界。
## 方案概述
展示层调用现有读取组件。
## 关键业务流程
展示层发起查询，返回当前状态。
## 异常处理与边界条件
状态缺失时展示原因。
## 兼容性与风险
维持现有输入，回滚时移除新增入口。
## FR 与技术设计的追踪矩阵
{trace}
## 技术决策
复用已有读取逻辑，避免重复存储。
## 待确认问题
{questions}
## 下一阶段测试设计的输入约定
测试读取已有状态和状态缺失两种场景。
"""


def test_design_requires_verified_snapshot(repositories, dev_workflow):
    _, _, source = repositories
    started = workflow_report(dev_workflow, source, "start", "design-gate")
    task_id = started["task_id"]
    denied = workflow(dev_workflow, source, "design-input", task_id)
    assert denied.returncode == 1 and not denied.stdout
    task = approve_requirements(dev_workflow, source, started)
    assert (task["current_stage"], task["status"]) == ("design", "running")
    snapshot = Path(task["requirements_snapshot"])
    original = snapshot.read_bytes()
    snapshot.unlink()
    missing = workflow(dev_workflow, source, "design-input", task_id)
    assert missing.returncode == 1 and "Requirements path is missing" in missing.stderr
    assert not Path(task["design_path"]).exists()
    snapshot.write_bytes(original + b"\n")
    damaged = workflow(dev_workflow, source, "design-input", task_id)
    assert damaged.returncode == 1 and "SHA-256 mismatch" in damaged.stderr
    snapshot.write_bytes(original)
    assert workflow_report(dev_workflow, source, "design-input", task_id)["approved_requirements"] == original.decode()

    # A step-three approval record with only a SHA cannot activate Design.
    record = source / "runtime" / "state" / "dev-workflow" / task_id / "state.json"
    legacy = json.loads(record.read_text(encoding="utf-8"))
    legacy.update(status="not_implemented", requirements_approval_id=None)
    record.write_text(json.dumps(legacy), encoding="utf-8")
    continued = workflow(dev_workflow, source, "continue", task_id)
    assert continued.returncode == 1 and "No approved Requirements snapshot" in continued.stderr
    revised = workflow_report(dev_workflow, source, "revise-requirements", task_id)
    assert revised["approval_history"] == []


def test_design_review_pauses_and_requires_approval(repositories, dev_workflow):
    _, _, source = repositories
    task = approve_requirements(dev_workflow, source, workflow_report(dev_workflow, source, "start", "design-review"))
    task_id = task["task_id"]
    target = Path(task["design_path"])
    assert target == Path(task["worktree_path"]) / "docs" / "changes" / task_id / "design.md"
    assert not (source / "docs" / "changes").exists()
    missing = workflow(dev_workflow, source, "submit", task_id, *stage_args(task))
    assert missing.returncode == 1 and "Requirements path is missing" in missing.stderr
    assert workflow(dev_workflow, source, "approve", task_id, task["requirements_sha256"], *stage_args(task)).returncode == 1
    target.write_text(design_draft(task, trace="- FR-002: status | reader | 未批准需求"), encoding="utf-8")
    incomplete = workflow(dev_workflow, source, "submit", task_id, *stage_args(task))
    assert incomplete.returncode == 1 and "all and only the approved FR" in incomplete.stderr

    target.write_text(design_draft(task, questions="- Q-001 展示入口需要兼容旧客户端吗？"), encoding="utf-8")
    waiting = workflow_report(dev_workflow, source, "submit", task_id, *stage_args(task))
    assert waiting["status"] == "waiting_for_input"
    draft = target.read_bytes()
    assert workflow_report(dev_workflow, source, "pause", task_id)["paused_from"] == "waiting_for_input"
    assert workflow_report(dev_workflow, source, "resume", task_id) == {
        **waiting, "state_version": waiting["state_version"] + 2,
    }
    assert target.read_bytes() == draft
    assert workflow(dev_workflow, source, "continue", task_id).returncode == 1
    running = workflow_report(dev_workflow, source, "continue", task_id, "--with-input")

    target.write_text(design_draft(task, version=2), encoding="utf-8")
    review = workflow_report(dev_workflow, source, "submit", task_id, *stage_args(running))
    assert review["current_stage"] == "design" and review["status"] == "waiting_for_approval"
    assert workflow(dev_workflow, source, "approve", task_id, "0" * 64, *stage_args(review)).returncode == 1
    original = target.read_bytes()
    target.write_bytes(original + b"\n")
    stale = workflow(dev_workflow, source, "approve", task_id, review["design_sha256"], *stage_args(review))
    assert stale.returncode == 1 and "changed after submission" in stale.stderr
    target.write_bytes(original)
    approved = workflow_report(dev_workflow, source, "approve", task_id, review["design_sha256"], *stage_args(review))
    assert (approved["current_stage"], approved["status"]) == ("test_design", "running")
    assert approved["stages"]["design"] == "completed"
    assert approved["requirements_approval_id"] == task["requirements_approval_id"]
    assert Path(approved["design_snapshot"]).read_bytes() == original
    assert workflow_report(dev_workflow, source, "status", task_id) == approved
    revised = workflow_report(dev_workflow, source, "revise-requirements", task_id)
    assert revised["approval_history"] == [
        {
            "stage": "requirements", "approval_id": approved["requirements_approval_id"],
            "sha256": sha256(Path(approved["requirements_snapshot"]).read_bytes()).hexdigest(),
        },
        {
            "stage": "design", "approval_id": approved["design_approval_id"],
            "sha256": sha256(Path(approved["design_snapshot"]).read_bytes()).hexdigest(),
            "requirements_approval_id": approved["requirements_approval_id"],
            "requirements_sha256": approved["requirements_sha256"],
        },
    ]
    assert workflow_report(dev_workflow, source, "status", task_id) == revised


def test_design_tasks_are_isolated_and_old_design_cannot_survive_reapproval(repositories, dev_workflow):
    _, _, source = repositories
    first, second = (
        approve_requirements(dev_workflow, source, workflow_report(dev_workflow, source, "start", "same-design"))
        for _ in range(2)
    )
    first_id, second_id = first["task_id"], second["task_id"]
    first_doc, second_doc = Path(first["design_path"]), Path(second["design_path"])
    assert first_doc != second_doc and first["worktree_path"] != second["worktree_path"]
    first_doc.write_text(design_draft(first), encoding="utf-8")
    assert not second_doc.exists()
    first_review = workflow_report(dev_workflow, source, "submit", first_id, *stage_args(first))
    second_doc.write_text(design_draft(second, questions="- RQ-001 必须确认新的需求边界。"), encoding="utf-8")
    waiting = workflow_report(dev_workflow, source, "submit", second_id, *stage_args(second))
    assert waiting["status"] == "waiting_for_input" and waiting["requirements_revision_required"]
    rejected = workflow(dev_workflow, source, "continue", second_id, "--with-input")
    assert rejected.returncode == 1 and "requires a Requirements change" in rejected.stderr
    original = second_doc.read_bytes()
    worktrees = git(source, "worktree", "list", "--porcelain")
    reopened = workflow_report(dev_workflow, source, "revise-requirements", second_id)
    assert (reopened["current_stage"], reopened["status"]) == ("requirements", "running")
    assert reopened["requirements_snapshot"] is None and Path(second["requirements_snapshot"]).exists()
    updated = approve_requirements(dev_workflow, source, reopened)
    assert updated["requirements_sha256"] == second["requirements_sha256"]
    assert updated["requirements_approval_id"] != second["requirements_approval_id"]
    assert second_doc.read_bytes() == original
    old = workflow(dev_workflow, source, "submit", second_id, *stage_args(updated))
    assert old.returncode == 1 and "current Requirements approval" in old.stderr
    assert git(source, "worktree", "list", "--porcelain") == worktrees
    assert workflow_report(dev_workflow, source, "status", first_id) == first_review


def test_design_approval_save_failure_does_not_report_success(repositories, dev_workflow, monkeypatch, capsys):
    _, _, source = repositories
    task = approve_requirements(dev_workflow, source, workflow_report(dev_workflow, source, "start", "design-save"))
    Path(task["design_path"]).write_text(design_draft(task), encoding="utf-8")
    waiting = workflow_report(dev_workflow, source, "submit", task["task_id"], *stage_args(task))
    monkeypatch.setattr(orchestrator, "PROJECT_ROOT", source)

    def fail_save(path, state):
        raise PermissionError("simulated design state write failure")

    monkeypatch.setattr(orchestrator, "save", fail_save)
    monkeypatch.setattr(sys, "argv", [
        "dev_workflow.py", "approve", task["task_id"], waiting["design_sha256"], *stage_args(waiting),
    ])
    assert orchestrator.main() == 1
    output = capsys.readouterr()
    assert not output.out and "simulated design state write failure" in output.err
    assert workflow_report(dev_workflow, source, "status", task["task_id"]) == waiting


def test_old_design_session_cannot_submit_after_revision_or_replay_same_digest(repositories, dev_workflow):
    _, _, source = repositories
    task = approve_requirements(dev_workflow, source, workflow_report(dev_workflow, source, "start", "old-session"))
    task_id = task["task_id"]
    design_session = workflow_report(dev_workflow, source, "design-input", task_id)
    revised = workflow_report(dev_workflow, source, "revise-requirements", task_id)
    requirements_path, design_path = Path(task["requirements_path"]), Path(task["design_path"])
    design_path.write_text(design_draft(design_session), encoding="utf-8")
    docs = (requirements_path.read_bytes(), design_path.read_bytes())
    rejected = workflow(dev_workflow, source, "submit", task_id, *stage_args(design_session))
    assert rejected.returncode == 1 and not rejected.stdout and "Stale submit" in rejected.stderr
    wrong_stage = workflow(
        dev_workflow, source, "submit", task_id, "--stage", "design", "--version", str(revised["state_version"]),
    )
    assert wrong_stage.returncode == 1 and "Stale submit" in wrong_stage.stderr
    assert workflow(dev_workflow, source, "submit", task_id).returncode == 2
    assert workflow_report(dev_workflow, source, "status", task_id) == revised
    assert (requirements_path.read_bytes(), design_path.read_bytes()) == docs

    waiting = workflow_report(dev_workflow, source, "submit", task_id, *stage_args(revised))
    running = workflow_report(dev_workflow, source, "continue", task_id)
    replay_submit = workflow(dev_workflow, source, "submit", task_id, *stage_args(revised))
    assert replay_submit.returncode == 1 and "Stale submit" in replay_submit.stderr
    latest = workflow_report(dev_workflow, source, "submit", task_id, *stage_args(running))
    assert waiting["requirements_sha256"] == latest["requirements_sha256"]
    replay_approve = workflow(
        dev_workflow, source, "approve", task_id, waiting["requirements_sha256"], *stage_args(waiting),
    )
    assert replay_approve.returncode == 1 and not replay_approve.stdout and "Stale approve" in replay_approve.stderr
    assert workflow_report(dev_workflow, source, "status", task_id) == latest
    assert (requirements_path.read_bytes(), design_path.read_bytes()) == docs
    approved = workflow_report(
        dev_workflow, source, "approve", task_id, latest["requirements_sha256"], *stage_args(latest),
    )
    assert approved["current_stage"] == "design"


def test_concurrent_submit_and_approve_only_commit_once(repositories, dev_workflow):
    _, _, source = repositories
    task = workflow_report(dev_workflow, source, "start", "parallel-review")
    task_id = task["task_id"]
    draft = Path(task["requirements_path"])
    draft.write_text(requirements_draft("无"), encoding="utf-8")
    original = draft.read_bytes()

    def race(operation, *args):
        with ThreadPoolExecutor(max_workers=2) as pool:
            attempts = [pool.submit(workflow, dev_workflow, source, operation, task_id, *args) for _ in range(2)]
            results = [attempt.result() for attempt in attempts]
        assert sorted(result.returncode for result in results) == [0, 1]
        winner = next(result for result in results if result.returncode == 0)
        loser = next(result for result in results if result.returncode != 0)
        assert not loser.stdout and any(
            marker in loser.stderr for marker in (f"Stale {operation}", "is being updated")
        )
        return json.loads(winner.stdout)

    submitted = race("submit", *stage_args(task))
    assert submitted["state_version"] == task["state_version"] + 1
    approved = race("approve", submitted["requirements_sha256"], *stage_args(submitted))
    assert approved["state_version"] == submitted["state_version"] + 1
    assert approved["current_stage"] == "design"
    snapshot = Path(approved["requirements_snapshot"])
    assert list(snapshot.parent.glob("r-*.md")) == [snapshot]
    assert snapshot.read_bytes() == draft.read_bytes() == original
    assert workflow_report(dev_workflow, source, "status", task_id) == approved


@pytest.mark.parametrize("stage", ["requirements", "design"])
@pytest.mark.parametrize("malformation", ["html-comment", "html-block", "fenced-question", "mixed-questions", "indented-record"])
def test_review_rejects_hidden_or_ambiguous_markdown(stage, malformation):
    meta = dict(task_id="a" * 32, requirements_sha256="b" * 64, requirements_approval_id="c" * 32)
    document = requirements_draft("无") if stage == "requirements" else design_draft(meta)
    if malformation == "html-comment":
        document = f"<!--\n{document}\n## Appendix\n-->\n"
    elif malformation == "html-block":
        document = f"<script>\n{document}\n## Appendix\n</script>\n"
    elif malformation == "fenced-question":
        question = "Q-001" if stage == "requirements" else "RQ-001"
        document = document.replace("\n无\n", f"\n无\n```\n- {question} Still open\n```\n")
    elif malformation == "mixed-questions":
        document = document.replace("\n无\n", "\n无\n- Q-001 Still open\n")
    else:
        document = document.replace("\n- FR-001", "\n    - FR-001", 1)
    with pytest.raises(RequirementsError):
        if stage == "requirements":
            review_requirements(document.encode("utf-8"))
        else:
            review_design(document.encode("utf-8"), functions={"FR-001"}, **meta)


def test_prose_and_code_examples_do_not_define_identifiers():
    example = "\n示例中的 FR-999 和 AC-999 仅用于说明。\n```text\n- FR-999 Sample\n- AC-999 Sample\n```\n"
    req = requirements_draft("无").replace("## 功能需求", f"{example}\n## 功能需求")
    assert review_requirements(req.encode("utf-8")) == {"FR-001"}
    meta = dict(task_id="a" * 32, requirements_sha256="b" * 64, requirements_approval_id="c" * 32)
    draft = design_draft(meta).replace("## FR 与技术设计的追踪矩阵", f"{example}\n## FR 与技术设计的追踪矩阵")
    assert review_design(draft.encode("utf-8"), functions={"FR-001"}, **meta).ready


def test_cli_rejects_original_markdown_bypasses_without_advancing(repositories, dev_workflow):
    _, _, source = repositories
    task = workflow_report(dev_workflow, source, "start", "markdown-gate")
    task_id = task["task_id"]
    requirements = Path(task["requirements_path"])
    requirements.write_text(f"<!--\n{requirements_draft('无')}\n## Appendix\n-->\n", encoding="utf-8")
    hidden = workflow(dev_workflow, source, "submit", task_id, *stage_args(task))
    assert hidden.returncode == 1 and not hidden.stdout and "Raw HTML" in hidden.stderr
    assert workflow_report(dev_workflow, source, "status", task_id) == task

    design = approve_requirements(dev_workflow, source, task)
    draft = Path(design["design_path"])
    draft.write_text(design_draft(design, questions="无\n```\n- RQ-001 Still open\n```"), encoding="utf-8")
    blocked = workflow(dev_workflow, source, "submit", task_id, *stage_args(design))
    assert blocked.returncode == 1 and not blocked.stdout and "Fenced blocks" in blocked.stderr
    assert workflow_report(dev_workflow, source, "status", task_id) == design
