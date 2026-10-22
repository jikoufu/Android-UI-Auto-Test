"""Offline checks for manual workflow references and truthful result states."""

import json
import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from agent_harness.evaluations.validate import relative_path, validate_task, validate_workflows
from agent_harness.schemas.protocol import CheckRecord, VerificationRecord, Workflow


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def protocol_root(tmp_path: Path) -> Path:
    for directory in ("agent_harness", "tasks"):
        shutil.copytree(ROOT / directory, tmp_path / directory, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copyfile(ROOT / "AGENTS.md", tmp_path / "AGENTS.md")
    return tmp_path


def make_task(root: Path, *, workflow: str = "feature_development", status: str = "active") -> Path:
    task_dir = root / "tasks/ENG-002"
    shutil.copytree(root / "tasks/_template", task_dir)
    path = task_dir / "task.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    value.update(
        task_id="ENG-002",
        template=False,
        title="验证文件协议",
        workflow=workflow,
        status=status,
        acceptance_criteria=["记录中未执行检查不能声称通过"],
    )
    path.write_text(yaml.safe_dump(value, allow_unicode=True), encoding="utf-8")
    return task_dir


def passing_verification() -> dict:
    return {
        "protocol_version": 1,
        "status": "passed",
        "review_mode": "self",
        "reviewer": "offline-test-fixture",
        "checks": [{"command": "example-check", "result": "passed", "exit_code": 0, "summary": "fixture result"}],
        "limitations": [],
    }


def test_repository_workflows_and_template_are_valid() -> None:
    """验证实际工作流的技能引用可用，任务模板保持未执行。"""
    # Step 1：校验仓库中的所有工作流和模板文件。
    workflows = validate_workflows(ROOT)
    template = validate_task(ROOT, ROOT / "tasks/_template", workflows)

    # Step 2：确认四类工作流均可使用且模板未冒充完成任务。
    assert set(workflows) == {"feature_development", "test_development", "test_delivery", "defect_review"}
    assert template.status == "draft"


def test_missing_skill_is_rejected(protocol_root: Path) -> None:
    """验证工作流引用的技能缺失时明确失败。"""
    # Step 1：在隔离目录移除工作流必需的技能。
    (protocol_root / "agent_harness/skills/implementation/SKILL.md").unlink()

    # Step 2：确认离线校验不会忽略断开的技能引用。
    with pytest.raises(ValueError, match="missing referenced file"):
        validate_workflows(protocol_root)


@pytest.mark.parametrize("needs", [["later"], ["first"]])
def test_forward_or_self_step_dependencies_are_rejected(needs: list[str]) -> None:
    """验证流程步骤不能依赖后续步骤或自身。"""
    # Step 1：准备包含无效依赖顺序的工作流。
    value = {
        "protocol_version": 1,
        "name": "example",
        "description": "example",
        "policies": ["AGENTS.md"],
        "steps": [{"id": "first", "skill": "SKILL.md", "needs": needs, "outputs": []}],
    }

    # Step 2：确认执行顺序不成立时拒绝该流程。
    with pytest.raises(ValidationError, match="preceding steps"):
        Workflow.model_validate(value)


@pytest.mark.parametrize("path", ["../outside.md", "/outside.md", "C:/outside.md", "..\\outside.md"])
def test_references_cannot_escape_the_repository(path: str) -> None:
    """验证文件引用拒绝绝对路径和向上越界。"""
    # Step 1：使用不同平台的越界引用执行路径校验。
    with pytest.raises(ValueError, match="portable relative paths"):
        relative_path(path)


@pytest.mark.parametrize(
    ("result", "exit_code"),
    [("passed", None), ("passed", 1), ("failed", 0), ("blocked", 0), ("not_run", 0), ("skipped", 0)],
)
def test_check_outcomes_must_match_execution(result: str, exit_code: int | None) -> None:
    """验证检查结果不能与退出码矛盾。"""
    # Step 1：尝试记录缺少成功执行依据或状态矛盾的检查。
    with pytest.raises(ValidationError):
        CheckRecord(command="example-check", result=result, exit_code=exit_code, summary="fixture result")


def test_no_checks_cannot_be_reported_as_passed() -> None:
    """验证空检查列表不能形成通过结论。"""
    # Step 1：移除所有执行记录，仅保留通过声明。
    value = passing_verification()
    value["checks"] = []

    # Step 2：确认模型拒绝没有实际检查的通过状态。
    with pytest.raises(ValidationError, match="executed passing checks"):
        VerificationRecord.model_validate(value)


def test_independent_review_requires_a_named_reviewer() -> None:
    """验证独立审阅声明必须指出实际执行者。"""
    # Step 1：准备未分配验证者的独立审阅记录。
    value = passing_verification()
    value.update(review_mode="independent", reviewer=None)

    # Step 2：确认缺少执行者时不能标记独立验证。
    with pytest.raises(ValidationError, match="reviewer identifier"):
        VerificationRecord.model_validate(value)


def test_partial_verification_records_unavailable_checks() -> None:
    """验证部分检查通过时仍保留环境阻塞和未验证范围。"""
    # Step 1：准备一个成功检查和一个缺少环境的检查。
    value = passing_verification()
    value.update(status="partial", limitations=["External environment unavailable."])
    value["checks"].append(
        {"command": "integration-check", "result": "blocked", "exit_code": None, "summary": "environment unavailable"}
    )

    # Step 2：确认部分验证可以记录，但不能改写为全部通过。
    assert VerificationRecord.model_validate(value).status == "partial"
    value["status"] = "passed"
    with pytest.raises(ValidationError, match="executed passing checks"):
        VerificationRecord.model_validate(value)


def test_complete_task_requires_executed_verification(protocol_root: Path) -> None:
    """验证保留未执行模板结果的任务不能宣称已完成。"""
    # Step 1：创建状态为完成但尚未执行检查的隔离任务。
    task_dir = make_task(protocol_root, status="complete")
    workflows = validate_workflows(protocol_root)

    # Step 2：确认任务状态与验证状态不一致时校验失败。
    with pytest.raises(ValueError, match="completed tasks require passing"):
        validate_task(protocol_root, task_dir, workflows)


def test_complete_delivery_requires_review_artifact(protocol_root: Path) -> None:
    """验证测试交付完成前必须产生工作流要求的审核记录。"""
    # Step 1：创建检查通过但缺少审核文件的交付任务。
    task_dir = make_task(protocol_root, workflow="test_delivery", status="complete")
    (task_dir / "verification.json").write_text(json.dumps(passing_verification()), encoding="utf-8")
    workflows = validate_workflows(protocol_root)

    # Step 2：确认缺少审核输出时失败，补齐输出后才能完成。
    with pytest.raises(ValueError, match="missing referenced file: review.md"):
        validate_task(protocol_root, task_dir, workflows)
    (task_dir / "review.md").write_text("Fixture review output.", encoding="utf-8")
    assert validate_task(protocol_root, task_dir, workflows).status == "complete"


def test_task_cannot_reference_an_unknown_workflow(protocol_root: Path) -> None:
    """验证未知工作流不会被任务记录默默接受。"""
    # Step 1：建立指向不存在工作流的任务。
    task_dir = make_task(protocol_root, workflow="unknown")

    # Step 2：确认配置失配时立即报错。
    with pytest.raises(ValueError, match="unknown workflow"):
        validate_task(protocol_root, task_dir, validate_workflows(protocol_root))
