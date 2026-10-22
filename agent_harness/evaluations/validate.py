"""Read-only validation for the Harness file protocol, not an Agent runner."""

import argparse
import json
import re
from pathlib import Path, PurePosixPath

import yaml
from pydantic import ValidationError

from agent_harness.schemas.protocol import TaskRecord, VerificationRecord, Workflow


def relative_path(value: str) -> PurePosixPath:
    """Require portable relative paths without escaping the containing directory."""
    path = PurePosixPath(value)
    if not value or "\\" in value or ":" in value or path.is_absolute() or ".." in path.parts:
        raise ValueError("references must use portable relative paths without parent traversal")
    return path


def referenced_file(base: Path, value: str) -> Path:
    path = (base / relative_path(value)).resolve()
    if not path.is_relative_to(base.resolve()):
        raise ValueError("references must stay inside their containing directory")
    if not path.is_file():
        raise ValueError(f"missing referenced file: {value}")
    return path


def read_yaml(path: Path) -> dict:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ValueError(f"invalid YAML in {path.name}") from error
    if not isinstance(value, dict):
        raise ValueError(f"expected a YAML mapping in {path.name}")
    return value


def validate_skill(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError("skills require YAML frontmatter")
    parts = text.split("---", 2)
    if len(parts) != 3 or not parts[2].strip():
        raise ValueError("skills require frontmatter and instructions")
    try:
        metadata = yaml.safe_load(parts[1])
    except yaml.YAMLError as error:
        raise ValueError("invalid skill frontmatter") from error
    if not isinstance(metadata, dict):
        raise ValueError("skill frontmatter must be a mapping")
    name = metadata.get("name")
    description = metadata.get("description")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9-]{1,64}", name):
        raise ValueError("skill name must use lowercase letters, digits and hyphens")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("skills require a description")


def validate_workflows(root: Path) -> dict[str, Workflow]:
    workflows: dict[str, Workflow] = {}
    for path in sorted((root / "agent_harness/workflows").glob("*.yaml")):
        workflow = Workflow.model_validate(read_yaml(path))
        if workflow.name != path.stem:
            raise ValueError("workflow name must match its filename")
        for policy in workflow.policies:
            referenced_file(root, policy)
        for step in workflow.steps:
            skill = referenced_file(root, step.skill)
            if skill.name != "SKILL.md":
                raise ValueError("workflow skills must reference SKILL.md")
            validate_skill(skill)
            for output in step.outputs:
                relative_path(output)
        workflows[workflow.name] = workflow
    if not workflows:
        raise ValueError("no workflow definitions found")
    return workflows


def validate_task(root: Path, task_dir: Path, workflows: dict[str, Workflow]) -> TaskRecord:
    task_dir = task_dir.resolve()
    if not task_dir.is_relative_to((root / "tasks").resolve()):
        raise ValueError("task records must stay inside tasks/")
    task = TaskRecord.model_validate(read_yaml(referenced_file(task_dir, "task.yaml")))
    if task.template != (task_dir == (root / "tasks/_template").resolve()):
        raise ValueError("only tasks/_template may be marked as a template")
    if not task.template and task.task_id != task_dir.name:
        raise ValueError("task_id must match its directory name")
    if task.workflow not in workflows:
        raise ValueError("task references an unknown workflow")
    for path in task.artifacts.model_dump().values():
        referenced_file(task_dir, path)
    verification_path = referenced_file(task_dir, task.artifacts.verification)
    try:
        raw_verification = json.loads(verification_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError("invalid verification JSON") from error
    verification = VerificationRecord.model_validate(raw_verification)
    for check in verification.checks:
        for evidence in check.evidence:
            relative_path(evidence)
    if task.template and verification.status != "not_run":
        raise ValueError("task templates cannot claim executed verification")
    if task.status == "complete":
        if verification.status != "passed":
            raise ValueError("completed tasks require passing verification")
        for step in workflows[task.workflow].steps:
            for output in step.outputs:
                referenced_file(task_dir, output)
    return task


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", help="Optional task directory relative to the repository root")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    try:
        workflows = validate_workflows(root)
        validate_task(root, root / "tasks/_template", workflows)
        if args.task:
            validate_task(root, root / relative_path(args.task), workflows)
    except (OSError, ValueError) as error:
        if isinstance(error, ValidationError):
            details = "; ".join(item["msg"] for item in error.errors(include_input=False))
        else:
            details = str(error)
        print(f"Harness validation failed: {details}")
        return 1
    print(f"Harness valid: {len(workflows)} workflows, task template" + (", requested task" if args.task else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
