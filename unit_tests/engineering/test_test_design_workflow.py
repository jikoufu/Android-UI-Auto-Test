"""Exercise the Test Design stage in temporary Git worktrees."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, distribution
import json
import os
from pathlib import Path
from shutil import copy2
import sys
import venv
from zipfile import ZipFile

import pytest

from agent_harness.workflows.pytest_evidence import run_cases
from agent_harness.workflows import dev_workflow as orchestrator
from agent_harness.workflows.requirements_document import RequirementsError
from agent_harness.workflows.test_design_document import PlanCase
from unit_tests.engineering.test_git_workspace_skill import (
    approve_requirements, design_draft, dev_workflow, git, repositories, stage_args, workflow, workflow_report,
)


@pytest.fixture(scope="session")
def offline_python(tmp_path_factory):
    configured = os.environ.get("TV_TEST_DESIGN_PYTHON")
    if configured:
        return configured
    root = tmp_path_factory.mktemp("test-design-venv")
    venv.EnvBuilder().create(root)
    if os.name == "nt":
        site_packages = root / "Lib/site-packages"
        python = root / "Scripts/python.exe"
    else:
        site_packages = root / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
        python = root / "bin/python"
    # Reuse the test suite's installed pytest wheels without an index or editable hooks.
    for name in ("pytest", "pluggy", "iniconfig", "packaging", "pygments", "colorama", "exceptiongroup", "tomli"):
        try:
            package = distribution(name)
        except PackageNotFoundError:
            continue
        for entry in package.files or ():
            if ".." in entry.parts or "__pycache__" in entry.parts:
                continue
            source = package.locate_file(entry)
            if source.is_file():
                target = site_packages / entry
                target.parent.mkdir(parents=True, exist_ok=True)
                copy2(source, target)
    return str(python)


@pytest.fixture(autouse=True)
def configure_offline_python(offline_python, monkeypatch):
    monkeypatch.setenv("TV_TEST_DESIGN_PYTHON", offline_python)


def approve_design(script, source, task):
    Path(task["design_path"]).write_text(design_draft(task), encoding="utf-8")
    pending = workflow_report(script, source, "submit", task["task_id"], *stage_args(task))
    return workflow_report(script, source, "approve", task["task_id"], pending["design_sha256"], *stage_args(pending))


def start(script, source, slug):
    task = workflow_report(script, source, "start", slug)
    return approve_design(script, source, approve_requirements(script, source, task))


def write_plan(task, *, fr="FR-001", questions="无"):
    path = Path(task["test_plan_path"])
    metadata = "\n".join(
        f"- {key}: {task[key]}" for key in
        ("task_id", "requirements_sha256", "requirements_approval_id", "design_sha256", "design_approval_id")
    )
    path.write_text(f"""# Test plan
## 文档元数据
{metadata}
- test_plan_version: 1
## 测试目标与范围
Check the synthetic status.
## 测试层级及选择理由
Unit level isolates status mapping.
## 测试场景和用例
TC-001 checks the expected status.
## 测试数据与环境依赖
Local Python and pytest only.
## Mock / Fixture 设计
Use a deterministic fixture.
## FR / AC / 测试追踪矩阵
- {fr} | AC-001 | TC-001 | red | unit_tests/test_design/test_status.py::test_status | status should be ready
## 自动化与人工测试边界
The synthetic check runs offline.
## Red 测试执行结果
Execution evidence is stored by the orchestrator.
## 已知限制和风险
No real devices or services.
## 待确认问题
{questions}
""", encoding="utf-8")
    return path


def test_test_design_requires_approved_inputs(repositories, dev_workflow):
    _, _, source = repositories
    task = workflow_report(dev_workflow, source, "start", "test-gate")
    task_id = task["task_id"]
    assert workflow(dev_workflow, source, "test-design-input", task_id).returncode == 1
    task = approve_requirements(dev_workflow, source, task)
    assert workflow(dev_workflow, source, "test-design-input", task_id).returncode == 1
    task = approve_design(dev_workflow, source, task)
    for field in ("requirements_snapshot", "design_snapshot"):
        path = Path(task[field])
        original = path.read_bytes()
        path.write_bytes(original + b"\n")
        result = workflow(dev_workflow, source, "test-design-input", task_id)
        assert result.returncode == 1 and "SHA-256 mismatch" in result.stderr
        path.write_bytes(original)
    payload = workflow_report(dev_workflow, source, "test-design-input", task_id)
    assert payload["approved_design"] and payload["approved_requirements"]
    missing = workflow(dev_workflow, source, "submit", task_id, *stage_args(task))
    assert missing.returncode == 1 and "missing" in missing.stderr
    write_plan(task, fr="FR-002")
    uncovered = workflow(dev_workflow, source, "submit", task_id, *stage_args(task))
    assert uncovered.returncode == 1 and "FR/AC" in uncovered.stderr


def test_test_design_approval_binds_red_evidence_and_code(repositories, dev_workflow):
    _, _, source = repositories
    task = start(dev_workflow, source, "test-approval")
    task_id = task["task_id"]
    worktree = Path(task["worktree_path"])
    plan = write_plan(task)
    test = worktree / "unit_tests/test_design/test_status.py"
    test.parent.mkdir(parents=True)
    test.write_text(
        'from pathlib import Path\n\ndef test_status():\n'
        '    assert Path("tracked.txt").read_text().strip() == "ready", "status should be ready"\n',
        encoding="utf-8",
    )
    assert not (source / "unit_tests/test_design/test_status.py").exists()
    assert workflow(dev_workflow, source, "submit", task_id, *stage_args(task)).returncode == 1
    run = workflow_report(dev_workflow, source, "test-run", task_id, *stage_args(task))
    assert run["test_evidence"]["exit_code"] == 1
    assert run["test_evidence"]["outcomes"]["TC-001"]["status"] == "red"
    assert run["test_evidence"]["trace"] == [{"fr": "FR-001", "ac": "AC-001", "case": "TC-001"}]
    print(f"TDD fixture: {run['test_evidence']['outcomes']['TC-001']}; pytest exit={run['test_evidence']['exit_code']}")
    workflow_report(dev_workflow, source, "pause", task_id)
    resumed = workflow_report(dev_workflow, source, "resume", task_id)
    assert plan.is_file() and test.is_file() and resumed["test_evidence"] == run["test_evidence"]
    assert workflow(dev_workflow, source, "submit", task_id, *stage_args(run)).returncode == 1
    with ThreadPoolExecutor(max_workers=2) as pool:
        attempts = [pool.submit(workflow, dev_workflow, source, "submit", task_id, *stage_args(resumed)) for _ in range(2)]
        results = [attempt.result() for attempt in attempts]
    assert sorted(result.returncode for result in results) == [0, 1]
    submitted = json.loads(next(result.stdout for result in results if result.returncode == 0))
    assert submitted["status"] == "waiting_for_approval" and submitted["current_stage"] == "test_design"
    assert workflow(dev_workflow, source, "approve", task_id, "0" * 64, *stage_args(submitted)).returncode == 1
    original = test.read_bytes()
    test.write_bytes(original + b"\n")
    assert workflow(dev_workflow, source, "approve", task_id, submitted["test_design_sha256"], *stage_args(submitted)).returncode == 1
    test.write_bytes(original)
    approved = workflow_report(dev_workflow, source, "approve", task_id, submitted["test_design_sha256"], *stage_args(submitted))
    assert (approved["current_stage"], approved["status"]) == ("development", "not_implemented")
    assert Path(approved["test_design_snapshot"]).read_bytes() == plan.read_bytes()
    with ZipFile(approved["test_design_archive"]) as archive:
        assert archive.read("test-plan.md") == plan.read_bytes()
        assert archive.read("files/unit_tests/test_design/test_status.py") == original
        assert json.loads(archive.read("evidence.json")) == run["test_evidence"]
    assert workflow_report(dev_workflow, source, "status", task_id) == approved
    test.write_bytes(original + b"\n")
    stale = workflow_report(dev_workflow, source, "status", task_id)
    assert (stale["current_stage"], stale["status"]) == ("test_design", "failed")
    assert stale["test_design_approval_id"] is None and "invalidated" in stale["error"]


def test_stale_data_evidence_and_edit_during_approval_are_rejected(repositories, dev_workflow, monkeypatch):
    _, upstream, source = repositories
    data = upstream / "data/status.txt"
    data.parent.mkdir()
    data.write_text("pending", encoding="utf-8")
    git(upstream, "add", "data/status.txt")
    git(upstream, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "Add fixture")
    git(upstream, "push", "origin", "master")
    task = start(dev_workflow, source, "stale-data")
    task_id = task["task_id"]
    worktree = Path(task["worktree_path"])
    write_plan(task)
    test = worktree / "unit_tests/test_design/test_status.py"
    test.parent.mkdir(parents=True)
    original = (
        b'from pathlib import Path\n\ndef test_status():\n'
        b'    assert Path("data/status.txt").read_text() == "ready", "status should be ready"\n'
    )
    test.write_bytes(original)
    red = workflow_report(dev_workflow, source, "test-run", task_id, *stage_args(task))
    assert red["test_evidence"]["outcomes"]["TC-001"]["status"] == "red"
    data = worktree / "data/status.txt"
    data.write_text("ready", encoding="utf-8")
    stale = workflow(dev_workflow, source, "submit", task_id, *stage_args(red))
    assert stale.returncode == 1 and "current plan and tests" in stale.stderr
    green = workflow_report(dev_workflow, source, "test-run", task_id, *stage_args(red))
    assert green["test_evidence"]["outcomes"]["TC-001"]["status"] == "unexpected_green"
    data.write_text("pending", encoding="utf-8")
    red = workflow_report(dev_workflow, source, "test-run", task_id, *stage_args(green))
    submitted = workflow_report(dev_workflow, source, "submit", task_id, *stage_args(red))
    monkeypatch.setattr(orchestrator, "PROJECT_ROOT", source)
    publish = orchestrator.publish_snapshot

    def edit_after_check(root, path, contents):
        if path.suffix == ".zip":
            test.write_bytes(original + b"\n# concurrent edit\n")
        publish(root, path, contents)

    monkeypatch.setattr(orchestrator, "publish_snapshot", edit_after_check)
    with pytest.raises((orchestrator.WorkflowError, RequirementsError)):
        orchestrator.task_command(
            orchestrator.state_root(), "approve", task_id, approval=submitted["test_design_sha256"],
            expected_stage="test_design", expected_version=submitted["state_version"],
        )
    assert workflow_report(dev_workflow, source, "status", task_id) == submitted
    assert test.read_bytes() != original


def test_run_and_approval_use_fixed_bytes_despite_last_moment_edit(repositories, dev_workflow, monkeypatch, capsys):
    """验证运行和审批绑定固定字节，最后落盘时改稿不能替换批准内容。"""
    # Step 1：运行期间修改并恢复工作副本，确认测试读取的是捕获版本。
    _, upstream, source = repositories
    data = upstream / "data/status.txt"
    data.parent.mkdir()
    data.write_text("ready", encoding="utf-8")
    git(upstream, "add", "data/status.txt")
    git(upstream, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "Add fixture")
    git(upstream, "push", "origin", "master")
    task = start(dev_workflow, source, "fixed-inputs")
    task_id = task["task_id"]
    worktree = Path(task["worktree_path"])
    write_plan(task)
    test = worktree / "unit_tests/test_design/test_status.py"
    test.parent.mkdir(parents=True)
    original = (
        b'from pathlib import Path\n\ndef test_status():\n'
        b'    assert Path("data/status.txt").read_text() == "ready", "status should be ready"\n'
    )
    test.write_bytes(original)
    live_data = worktree / "data/status.txt"
    monkeypatch.setattr(orchestrator, "PROJECT_ROOT", source)

    def edit_restore(directory, cases):
        assert directory != worktree
        live_data.write_bytes(b"pending")
        try:
            return run_cases(directory, cases)
        finally:
            live_data.write_bytes(b"ready")

    with monkeypatch.context() as changes:
        changes.setattr(orchestrator, "run_cases", edit_restore)
        result = orchestrator.task_command(
            orchestrator.state_root(), "test-run", task_id,
            expected_stage="test_design", expected_version=task["state_version"],
        ).report()
    assert result["test_evidence"]["outcomes"]["TC-001"]["status"] == "unexpected_green"
    denied = workflow(dev_workflow, source, "submit", task_id, *stage_args(result))
    assert denied.returncode == 1 and "outcomes did not match" in denied.stderr

    live_data.write_bytes(b"pending")
    red = workflow_report(dev_workflow, source, "test-run", task_id, *stage_args(result))
    submitted = workflow_report(dev_workflow, source, "submit", task_id, *stage_args(red))
    # Step 2：在最终原子替换的瞬间修改测试，审批仍只能引用原始归档。
    original_replace = Path.replace
    state_path = orchestrator.task_state_path(orchestrator.state_root(), task_id)

    def edit_at_replace(path, destination):
        if destination == state_path:
            test.write_bytes(original + b"\n# late edit\n")
        return original_replace(path, destination)

    monkeypatch.setattr(Path, "replace", edit_at_replace)
    monkeypatch.setattr("sys.argv", [
        "dev_workflow.py", "approve", task_id, submitted["test_design_sha256"], *stage_args(submitted),
    ])
    assert orchestrator.main() == 0
    output = capsys.readouterr()
    assert not output.err
    assert json.loads(output.out)["test_design_approval_scope"] == "verified_archive"
    persisted = orchestrator.load(state_path, task_id)
    captured = orchestrator.verify_approved_test_bundle(persisted, check_live=False)
    assert captured["unit_tests/test_design/test_status.py"] == original
    assert test.read_bytes() != original

    # Step 3：默认消费门禁拒绝已过期的工作副本，查询撤销批准并保留诊断。
    with pytest.raises(orchestrator.WorkflowError, match="current plan and tests"):
        orchestrator.verify_approved_test_bundle(persisted)
    stale = workflow_report(dev_workflow, source, "status", task_id)
    assert (stale["current_stage"], stale["status"]) == ("test_design", "failed")
    assert stale["test_design_approval_id"] is None
    assert stale["approval_history"][-1]["archive_sha256"] == persisted.test_design_archive_sha256


def test_repository_pytest_config_cannot_import_external_source(tmp_path):
    """验证项目 pytest 配置不能把临时副本外的模块引入证据运行。"""
    copy = tmp_path / "captured"
    outside = tmp_path / "external"
    outside.mkdir()
    (outside / "review_editable.py").write_text("value = 'pending'\n", encoding="utf-8")
    test = copy / "unit_tests/test_design/test_status.py"
    test.parent.mkdir(parents=True)
    (copy / "pytest.ini").write_text(f"[pytest]\npythonpath = {outside.as_posix()}\n", encoding="utf-8")
    (copy / "conftest.py").write_text("raise RuntimeError('root hook should not load')\n", encoding="utf-8")
    test.write_text(
        'import review_editable\n\ndef test_status():\n    assert review_editable.value == "ready"\n',
        encoding="utf-8",
    )
    evidence = run_cases(copy, {"TC-001": PlanCase("red", "unit_tests/test_design/test_status.py::test_status", "ready")})
    outcome = evidence["outcomes"]["TC-001"]
    assert outcome["status"] == "error" and "ModuleNotFoundError" in outcome["reason"]


def test_project_environment_is_rejected_before_collection(tmp_path, monkeypatch):
    """验证带 editable 启动钩子的虚拟环境在收集前被拒绝。"""
    root = tmp_path / "dependency-venv"
    python = root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.touch()
    (root / "pyvenv.cfg").write_text("include-system-site-packages = false\n", encoding="utf-8")
    packages = root / ("Lib/site-packages" if os.name == "nt" else "lib/python3.12/site-packages")
    packages.mkdir(parents=True)
    (packages / "editable.pth").write_text("import external_module\n", encoding="utf-8")
    monkeypatch.setenv("TV_TEST_DESIGN_PYTHON", str(python))
    file = tmp_path / "unit_tests/test_design/test_example.py"
    file.parent.mkdir(parents=True)
    file.write_text("def test_example(): pass\n", encoding="utf-8")
    with pytest.raises(RequirementsError, match="startup hooks"):
        run_cases(tmp_path, {"TC-001": PlanCase("pass", "unit_tests/test_design/test_example.py::test_example", "ok")})


def test_test_design_isolates_plans_and_waits_for_input(repositories, dev_workflow):
    _, _, source = repositories
    first, second = (start(dev_workflow, source, "test-isolation") for _ in range(2))
    write_plan(first, questions="- Q-001 Which fixture is available?")
    assert not Path(second["test_plan_path"]).exists()
    waiting = workflow_report(dev_workflow, source, "submit", first["task_id"], *stage_args(first))
    assert waiting["status"] == "waiting_for_input"
    assert workflow_report(dev_workflow, source, "status", second["task_id"]) == second


@pytest.mark.parametrize("launcher", [
    'subprocess.run([sys.executable, "-c", payload], check=False)',
    'subprocess.run([sys.executable, "-c", payload], shell=True)',
    'os.system("echo escaped > child-ran.txt")',
    'os.spawnv(os.P_WAIT, sys.executable, [sys.executable, "-c", payload])',
])
@pytest.mark.parametrize("phase", ["collection", "call"])
def test_child_process_attempt_cannot_be_red_even_when_caught(tmp_path, launcher, phase):
    """验证子进程在收集和执行时均被拒绝，吞掉异常也不能产生 Red。"""
    # Step 1：模拟子进程导入外部工程；即使测试吞掉异常后断言失败也必须拒绝证据。
    attempt = (
        f"try:\n    {launcher}\nexcept Exception:\n    pass\n"
    )
    body = (
        'import os, sys, subprocess\n'
        'payload = "from pathlib import Path; Path(\'child-ran.txt\').touch(); import automation_core"\n'
    )
    if phase == "collection":
        body += attempt + '\ndef test_status():\n    assert False, "status should be ready"\n'
    else:
        body += '\ndef test_status():\n' + ''.join('    ' + line + '\n' for line in attempt.splitlines())
        body += '    assert False, "status should be ready"\n'
    test = tmp_path / "unit_tests/test_design/test_status.py"
    test.parent.mkdir(parents=True)
    test.write_text(body, encoding="utf-8")

    # Step 2：检查失败分类和副作用，子进程不能被计入有效 Red。
    evidence = run_cases(tmp_path, {"TC-001": PlanCase("red", "unit_tests/test_design/test_status.py::test_status", "status should be ready")})
    outcome = evidence["outcomes"]["TC-001"]
    assert outcome["status"] == "error" and "external process attempt" in outcome["reason"]
    assert not (tmp_path / "child-ran.txt").exists()


def test_approval_rejects_legacy_evidence_and_preserves_state_on_write_failure(
    repositories, dev_workflow, monkeypatch, capsys,
):
    """验证旧证据不能重用，落盘失败不报告成功，批准归档必须通过校验。"""
    # Step 1：创建独立任务，运行符合当前策略的先行测试。
    _, _, source = repositories
    task = start(dev_workflow, source, "archive-validation")
    write_plan(task)
    test = Path(task["worktree_path"]) / "unit_tests/test_design/test_status.py"
    test.parent.mkdir(parents=True)
    original = b'def test_status():\n    assert False, "status should be ready"\n'
    test.write_bytes(original)
    run = workflow_report(dev_workflow, source, "test-run", task["task_id"], *stage_args(task))
    monkeypatch.setattr(orchestrator, "PROJECT_ROOT", source)
    state = orchestrator.task_state_path(orchestrator.state_root(), task["task_id"])
    record = orchestrator.load(state, task["task_id"])
    old_evidence = {key: value for key, value in record.test_evidence.items() if key != "policy"}
    with pytest.raises(orchestrator.WorkflowError, match="Run test-run"):
        orchestrator.test_bundle(replace(record, test_evidence=old_evidence), Path(task["test_plan_path"]).read_bytes())
    submitted = workflow_report(dev_workflow, source, "submit", task["task_id"], *stage_args(run))

    # Step 2：模拟最终替换失败，CLI 必须非零且保留待审批记录。
    original_replace = Path.replace

    def deny_state_replace(path, destination):
        if destination == state:
            raise PermissionError("simulated approval state failure")
        return original_replace(path, destination)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", deny_state_replace)
        patch.setattr(sys, "argv", ["dev_workflow.py", "approve", task["task_id"],
                                  submitted["test_design_sha256"], *stage_args(submitted)])
        assert orchestrator.main() == 1
    output = capsys.readouterr()
    assert not output.out and "simulated approval state failure" in output.err
    assert orchestrator.load(state, task["task_id"]).report() == submitted

    # Step 3：正常批准只返回归档字节；归档缺失、损坏和内容替换必须拒绝。
    approved = orchestrator.task_command(
        orchestrator.state_root(), "approve", task["task_id"], approval=submitted["test_design_sha256"],
        expected_stage="test_design", expected_version=submitted["state_version"],
    )
    captured = orchestrator.verify_approved_test_bundle(approved)
    assert captured["unit_tests/test_design/test_status.py"] == original
    archive = approved.test_design_archive()
    archive_data = archive.read_bytes()
    original_read = orchestrator.read_snapshot

    def busy_archive(root, path, digest):
        if path == archive:
            raise PermissionError("temporary sharing violation")
        return original_read(root, path, digest)

    with monkeypatch.context() as patch:
        patch.setattr(orchestrator, "read_snapshot", busy_archive)
        with pytest.raises(orchestrator.WorkflowError, match="Retry"):
            orchestrator.task_command(orchestrator.state_root(), "status", task["task_id"])
    assert orchestrator.task_command(orchestrator.state_root(), "status", task["task_id"]) == approved
    archive.unlink()
    with pytest.raises((RequirementsError, OSError)):
        orchestrator.verify_approved_test_bundle(approved)
    archive.write_bytes(b"invalid zip")
    with pytest.raises(RequirementsError, match="SHA-256 mismatch"):
        orchestrator.verify_approved_test_bundle(approved)
    with pytest.raises(orchestrator.WorkflowError, match="Invalid Test Design archive"):
        orchestrator.verify_approved_test_bundle(replace(
            approved, test_design_archive_sha256=sha256(b"invalid zip").hexdigest(),
        ))
    archive.write_bytes(archive_data)
    test.write_bytes(original + b"\n# unapproved edit\n")
    assert captured["unit_tests/test_design/test_status.py"] == original
    with pytest.raises(orchestrator.WorkflowError):
        orchestrator.verify_approved_test_bundle(approved)


@pytest.mark.parametrize(("code", "expected"), [
    ('def test_status():\n    assert False, "status should be ready"\n', "red"),
    ('def test_status():\n    assert 2 == 3, "different diagnostic"\n', "red"),
    ('def helper():\n    assert False\ndef test_status():\n    helper()\n', "error"),
    ('def test_status():\n    raise AssertionError("status should be ready")\n', "error"),
    ('def test_status():\n    pass\n', "unexpected_green"),
    ('def test_status():\n    assert\n', "error"),
    ('import no_such_test_design_dependency\ndef test_status():\n    pass\n', "error"),
    ('def test_status(missing_fixture):\n    pass\n', "error"),
    ('def test_status():\n    raise OSError("offline")\n', "error"),
    ('import pytest\n@pytest.mark.device\ndef test_status():\n    assert False, "status should be ready"\n', "error"),
])
def test_pytest_evidence_distinguishes_red_from_other_outcomes(tmp_path, code, expected):
    test = tmp_path / "unit_tests/test_design/test_status.py"
    test.parent.mkdir(parents=True)
    test.write_text(code, encoding="utf-8")
    evidence = run_cases(tmp_path, {"TC-001": PlanCase("red", "unit_tests/test_design/test_status.py::test_status", "status should be ready")})
    assert evidence["outcomes"]["TC-001"]["status"] == expected
    if expected == "red":
        assert evidence["exit_code"] == 1
        print(f"RED evidence: {evidence}")


@pytest.mark.parametrize("filename", ["tests/system/test_device.py", "unit_tests/engineering/test_side_effect.py"])
def test_modules_outside_offline_directory_are_not_collected(tmp_path, filename):
    """验证目录白名单在 pytest 导入模块前生效。"""
    test = tmp_path / filename
    test.parent.mkdir(parents=True)
    test.write_text(
        'from pathlib import Path\nPath("imported.txt").touch()\ndef test_only(): pass\n',
        encoding="utf-8",
    )
    with pytest.raises(RequirementsError, match="unit_tests/test_design"):
        run_cases(tmp_path, {"TC-001": PlanCase("pass", f"{filename}::test_only", "Pass")})
    assert not (tmp_path / "imported.txt").exists()


def test_conflicting_node_expectations_are_rejected(tmp_path):
    """验证同一 pytest 节点不能被两个用例分别声明为 Red 和 Pass。"""
    node = "unit_tests/test_design/test_status.py::test_status"
    with pytest.raises(RequirementsError, match="unique pytest nodes"):
        run_cases(tmp_path, {"TC-001": PlanCase("red", node, "fail"), "TC-002": PlanCase("pass", node, "pass")})


def test_deleted_test_is_not_an_input_unless_explicitly_selected(repositories):
    """验证删除旧测试后仍可捕获新测试，显式缺失路径继续报错。"""
    from agent_harness.workflows.test_design_document import Plan, test_inputs

    _, _, source = repositories
    old = source / "unit_tests/test_legacy.py"
    old.parent.mkdir()
    old.touch()
    git(source, "add", "unit_tests/test_legacy.py")
    git(source, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "Old test")
    base = git(source, "rev-parse", "HEAD")
    old.unlink()
    new = source / "unit_tests/test_design/test_status.py"
    new.parent.mkdir()
    new.write_text("def test_status(): pass\n", encoding="utf-8")
    cases = {"TC-001": PlanCase("pass", "unit_tests/test_design/test_status.py::test_status", "ok")}
    assert set(test_inputs(source, base, Plan(cases))) == {"unit_tests/test_design/test_status.py"}
    with pytest.raises(RequirementsError, match="missing"):
        test_inputs(source, base, Plan(cases, inputs=("unit_tests/test_legacy.py",)))
