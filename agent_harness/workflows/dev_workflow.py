"""Orchestrate the manually triggered development workflow."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Iterator
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

if __package__:
    from .acceptance_document import review_acceptance
    from .approved_artifacts import publish_snapshot, read_snapshot, snapshot_path
    from .design_document import review_design
    from .requirements_document import RequirementsError, artifact_path, document_path, review_requirements, verify_worktree
    from .requirements_document import GitReadError, checked_artifact, markdown_sections, requirements_trace
    from .test_design_document import PYTEST_CONFIG, TEST_ROOTS, Plan, bundle_archive, bundle_digest, execution_inputs, file_hashes, parse_plan, relative_input, test_inputs
    from .pytest_evidence import EVIDENCE_POLICY, run_cases
else:
    from acceptance_document import review_acceptance
    from approved_artifacts import publish_snapshot, read_snapshot, snapshot_path
    from design_document import review_design
    from requirements_document import RequirementsError, artifact_path, document_path, review_requirements, verify_worktree
    from requirements_document import GitReadError, checked_artifact, markdown_sections, requirements_trace
    from test_design_document import PYTEST_CONFIG, TEST_ROOTS, Plan, bundle_archive, bundle_digest, execution_inputs, file_hashes, parse_plan, relative_input, test_inputs
    from pytest_evidence import EVIDENCE_POLICY, run_cases


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GIT_WORKSPACE = Path(".agents/skills/git-workspace/scripts/git_workspace.py")
WORKSPACE_TIMEOUT = 900
STAGES = (
    "git_workspace", "requirements", "design", "test_design", "development", "acceptance",
    "integration_cleanup",
)
ACTIVE_STAGES = ("requirements", "design", "test_design", "development", "acceptance")
POST_TEST_DESIGN = ("development", "acceptance")
DRAFT_STATUSES = ("not_implemented", "running", "waiting_for_input", "waiting_for_approval", "failed")


class WorkflowError(Exception):
    pass


@dataclass(frozen=True)
class Task:
    task_id: str
    worktree_path: str | None
    branch: str | None
    base_commit: str | None
    current_stage: str
    status: str
    error: str | None = None
    paused_from: str | None = None
    requirements_sha256: str | None = None
    requirements_completed: bool = False
    requirements_approval_id: str | None = None
    requirements_revision_required: bool = False
    design_sha256: str | None = None
    design_approval_id: str | None = None
    state_version: int = 0
    approval_history: list[dict[str, object]] = field(default_factory=list)
    test_design_sha256: str | None = None
    test_design_approval_id: str | None = None
    test_design_archive_sha256: str | None = None
    test_evidence: dict | None = None
    development_sha256: str | None = None
    development_approval_id: str | None = None
    development_archive_sha256: str | None = None
    development_evidence: dict | None = None
    acceptance_evidence: dict | None = None
    acceptance_sha256: str | None = None
    acceptance_approval_id: str | None = None

    def requirements_path(self) -> Path:
        assert self.worktree_path is not None
        return artifact_path(Path(self.worktree_path), self.task_id)

    def approved_path(self, stage: str) -> Path | None:
        approval_id = getattr(self, f"{stage}_approval_id")
        if approval_id is None or self.worktree_path is None:
            return None
        return snapshot_path(Path(self.worktree_path), self.task_id, stage, approval_id)

    def test_design_archive(self) -> Path | None:
        snapshot = self.approved_path("test_design")
        return snapshot.with_suffix(".zip") if snapshot is not None else None

    def report(self) -> dict[str, object]:
        workspace_ready = self.current_stage != "git_workspace"
        stages = dict.fromkeys(STAGES, "not_implemented")
        stages["git_workspace"] = "completed" if workspace_ready else self.status
        if workspace_ready:
            stages["requirements"] = "completed" if self.requirements_completed else self.status
        if self.current_stage in ("design", "test_design", *POST_TEST_DESIGN):
            stages["design"] = self.status if self.current_stage == "design" else "completed"
        if self.current_stage in ("test_design", *POST_TEST_DESIGN):
            stages["test_design"] = self.status if self.current_stage == "test_design" else "completed"
        if self.current_stage in POST_TEST_DESIGN:
            stages["development"] = self.status if self.current_stage == "development" else "completed"
        if self.current_stage == "acceptance":
            stages["acceptance"] = "completed" if self.status == "awaiting_integration" else self.status
        requirements_snapshot = self.approved_path("requirements")
        design_snapshot = self.approved_path("design")
        return {
            **asdict(self),
            "stages": stages,
            "requirements_path": str(self.requirements_path()) if workspace_ready else None,
            "design_path": str(self.requirements_path().with_name("design.md")) if self.requirements_completed else None,
            "requirements_snapshot": str(requirements_snapshot) if requirements_snapshot is not None else None,
            "design_snapshot": str(design_snapshot) if design_snapshot is not None else None,
            "test_plan_path": str(self.requirements_path().with_name("test-plan.md")) if self.design_approval_id else None,
            "test_design_snapshot": str(self.approved_path("test_design")) if self.test_design_approval_id else None,
            "test_design_archive": str(self.test_design_archive()) if self.test_design_approval_id else None,
            "test_design_approval_scope": "verified_archive" if self.test_design_approval_id else None,
            "development_path": str(self.requirements_path().with_name("development.md")) if self.test_design_approval_id else None,
            "development_archive": str(self.approved_path("development").with_suffix(".zip")) if self.development_approval_id else None,
            "development_approval_scope": "verified_archive" if self.development_approval_id else None,
            "acceptance_path": str(self.requirements_path().with_name("acceptance.md")) if self.development_approval_id else None,
            "acceptance_snapshot": str(self.approved_path("acceptance")) if self.acceptance_approval_id else None,
            "next_stage": self.current_stage if workspace_ready and self.status not in ("failed", "awaiting_integration") else None,
        }


def state_root() -> Path:
    runtime = os.environ.get("TV_AUTOMATION_RUNTIME_DIR") or "runtime"
    return (PROJECT_ROOT / runtime).resolve() / "state" / "dev-workflow"


def save(
    path: Path, task: Task, *, verify_test_approval: bool = False, verify_development_approval: bool = False,
    verify_acceptance_approval: bool = False,
) -> None:
    # Replace a complete record so readers see either the old or the new state.
    stream = NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False)
    temporary = Path(stream.name)
    try:
        with stream:
            json.dump(asdict(task), stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if verify_test_approval:
            verify_approved_test_bundle(task, check_live=False)
        if verify_development_approval:
            verify_approved_development(task, check_live=False)
        if verify_acceptance_approval:
            verify_approved_acceptance(task)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_workspace(worktree_path: object, branch: object, base_commit: object) -> None:
    if not isinstance(worktree_path, str) or "\0" in worktree_path or not Path(worktree_path).is_absolute():
        raise WorkflowError("worktree_path must be an absolute path.")
    name = Path(worktree_path).name
    if (
        branch != f"codex/git-workspace/{name}"
        or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*-[0-9a-f]{32}", name)
    ):
        raise WorkflowError("branch must be a task branch matching worktree_path.")
    if not isinstance(base_commit, str) or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", base_commit):
        raise WorkflowError("base_commit must be a full Git commit SHA.")


def load(path: Path, task_id: str) -> Task:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(record, dict):
            # Ignore fields from earlier local workflow versions.
            record.pop("development_baseline_sha256", None)
            record.pop("development_handoff_sha256", None)
            record.pop("integration_commit", None)
        task = Task(**record)
    except FileNotFoundError as exc:
        raise WorkflowError(f"Task not found: {task_id}") from exc
    except (ValueError, TypeError) as exc:
        raise WorkflowError(f"Invalid task record: {path}") from exc

    valid_statuses = {
        "git_workspace": ("running", "failed"),
        "requirements": (*DRAFT_STATUSES, "paused"),
        "design": (*DRAFT_STATUSES, "paused"),
        "test_design": (*DRAFT_STATUSES, "paused"),
        "development": (*DRAFT_STATUSES, "paused"),
        "acceptance": (*DRAFT_STATUSES, "paused", "awaiting_integration"),
    }
    invalid = f"Invalid task record: {path}"
    if (
        task.task_id != task_id or not isinstance(task.current_stage, str)
        or task.status not in valid_statuses.get(task.current_stage, ())
        or type(task.state_version) is not int or task.state_version < 0
        or not isinstance(task.approval_history, list)
    ):
        raise WorkflowError(invalid)
    if task.current_stage != "git_workspace":
        try:
            validate_workspace(task.worktree_path, task.branch, task.base_commit)
        except WorkflowError as exc:
            raise WorkflowError(f"Invalid task record at {path}: {exc}") from exc
    elif any(value is not None for value in (task.worktree_path, task.branch, task.base_commit)):
        raise WorkflowError(f"Invalid task record at {path}: Git Workspace has not completed.")
    if task.paused_from is not None and (
        task.status != "paused" or task.paused_from not in DRAFT_STATUSES
    ):
        raise WorkflowError(invalid)
    if task.requirements_completed is not (task.current_stage in ("design", "test_design", *POST_TEST_DESIGN)):
        raise WorkflowError(invalid)
    if not isinstance(task.requirements_revision_required, bool) or (
        task.requirements_revision_required and (
            task.current_stage != "design" or "waiting_for_input" not in (task.status, task.paused_from)
        )
    ):
        raise WorkflowError(invalid)
    legacy_design = task.current_stage == "design" and task.status == "not_implemented"
    if task.requirements_completed and task.requirements_approval_id is None and not legacy_design:
        raise WorkflowError(invalid)
    for stage, completed, digest, approval_id in (
        ("requirements", task.requirements_completed, task.requirements_sha256, task.requirements_approval_id),
        ("design", task.current_stage in ("test_design", *POST_TEST_DESIGN), task.design_sha256, task.design_approval_id),
        ("test_design", task.current_stage in POST_TEST_DESIGN, task.test_design_sha256, task.test_design_approval_id),
        ("development", task.current_stage == "acceptance", task.development_sha256, task.development_approval_id),
        ("acceptance", task.status == "awaiting_integration", task.acceptance_sha256, task.acceptance_approval_id),
    ):
        has_digest = completed or (
            task.current_stage == stage and "waiting_for_approval" in (task.status, task.paused_from)
        )
        if has_digest:
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise WorkflowError(f"{invalid} Missing or invalid {stage}_sha256.")
        elif digest is not None:
            raise WorkflowError(invalid)
        if approval_id is not None and (
            not completed or not isinstance(approval_id, str) or not re.fullmatch(r"[0-9a-f]{32}", approval_id)
        ):
            raise WorkflowError(invalid)
    if task.current_stage in ("test_design", *POST_TEST_DESIGN) and task.design_approval_id is None:
        raise WorkflowError(invalid)
    if task.current_stage in POST_TEST_DESIGN and task.test_design_approval_id is None:
        raise WorkflowError(invalid)
    if task.test_design_archive_sha256 is not None and (
        task.current_stage not in POST_TEST_DESIGN or not isinstance(task.test_design_archive_sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", task.test_design_archive_sha256)
    ):
        raise WorkflowError(invalid)
    if task.test_evidence is not None and (
        task.current_stage not in ("test_design", *POST_TEST_DESIGN) or not isinstance(task.test_evidence, dict)
    ):
        raise WorkflowError(invalid)
    if task.current_stage == "acceptance" and not task.development_approval_id:
        raise WorkflowError(invalid)
    if task.development_archive_sha256 is not None and (
        task.current_stage != "acceptance" or not isinstance(task.development_archive_sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", task.development_archive_sha256)
    ):
        raise WorkflowError(invalid)
    if task.development_evidence is not None and (
        task.current_stage not in POST_TEST_DESIGN or not isinstance(task.development_evidence, dict)
    ):
        raise WorkflowError(invalid)
    if task.status == "awaiting_integration" and not task.acceptance_approval_id:
        raise WorkflowError(invalid)
    if task.acceptance_evidence is not None and (
        task.current_stage != "acceptance" or not isinstance(task.acceptance_evidence, dict)
    ):
        raise WorkflowError(invalid)
    return task


def create_workspace(slug: str) -> dict[str, str]:
    script = PROJECT_ROOT / GIT_WORKSPACE
    if not script.is_file():
        raise WorkflowError("The git-workspace Skill script is missing from this checkout.")
    try:
        result = subprocess.run(
            [sys.executable, "-X", "utf8", str(script), "create", "--", slug],
            cwd=PROJECT_ROOT, capture_output=True, stdin=subprocess.DEVNULL, text=True,
            encoding="utf-8", errors="replace", timeout=WORKSPACE_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise WorkflowError("git-workspace timed out. Inspect Git for a partially created worktree.") from exc
    except OSError as exc:
        raise WorkflowError("Could not start the git-workspace script.") from exc
    if result.returncode:
        raise WorkflowError(result.stderr.strip() or f"git-workspace exited with code {result.returncode}.")

    try:
        payload = json.loads(result.stdout)
        fields = {key: payload[key] for key in ("worktree_path", "branch", "base_commit")}
        validate_workspace(**fields)
    except (KeyError, TypeError, ValueError, WorkflowError) as exc:
        raise WorkflowError(
            "git-workspace returned invalid workspace metadata. Inspect Git before starting another task."
        ) from exc
    return fields


def start(root: Path, slug: str) -> Task:
    task = Task(
        task_id=uuid4().hex, worktree_path=None, branch=None, base_commit=None,
        current_stage="git_workspace", status="running",
    )
    directory = root / task.task_id
    directory.mkdir(parents=True)
    path = directory / "state.json"
    save(path, task)

    try:
        workspace = create_workspace(slug)
    except WorkflowError as exc:
        task = replace(task, status="failed", error=str(exc))
    else:
        task = prepare_stage(replace(task, **workspace, current_stage="requirements"))

    task = replace(task, state_version=1)
    try:
        save(path, task)
    except OSError as exc:
        message = f"Could not persist the final state for task {task.task_id} at {path}: {exc}"
        if task.worktree_path is not None:
            message += (
                f"\nCreated worktree: {task.worktree_path}\n"
                f"Task branch: {task.branch}\nBase commit: {task.base_commit}"
            )
        elif task.error is not None:
            message += f"\nGit Workspace error: {task.error}"
        raise WorkflowError(message) from exc
    return task


def task_document(task: Task, *, prepare: bool = False) -> Path:
    assert task.worktree_path is not None and task.branch is not None
    worktree = Path(task.worktree_path)
    verify_worktree(PROJECT_ROOT, worktree, task.branch)
    filename = "test-plan.md" if task.current_stage in ("test_design", *POST_TEST_DESIGN) else f"{task.current_stage}.md"
    return document_path(worktree, task.task_id, prepare=prepare, filename=filename)


def approved_requirements(task: Task, worktree: Path) -> tuple[bytes, set[str]]:
    snapshot = task.approved_path("requirements")
    if not task.requirements_completed or snapshot is None or task.requirements_sha256 is None:
        raise WorkflowError("No approved Requirements snapshot. Use revise-requirements and reapprove.")
    data = read_snapshot(worktree, snapshot, task.requirements_sha256)
    functions = review_requirements(data)
    if functions is None:
        raise WorkflowError("The approved Requirements snapshot still contains open questions.")
    return data, functions


def approved_test_inputs(task: Task, worktree: Path) -> tuple[bytes, bytes]:
    requirements, functions = approved_requirements(task, worktree)
    snapshot = task.approved_path("design")
    if snapshot is None or task.design_sha256 is None:
        raise WorkflowError("No approved Design snapshot.")
    design = read_snapshot(worktree, snapshot, task.design_sha256)
    if not review_design(
        design, task_id=task.task_id, requirements_sha256=task.requirements_sha256,
        requirements_approval_id=task.requirements_approval_id, functions=functions,
    ).ready:
        raise WorkflowError("The approved Design still contains open questions.")
    return requirements, design


def inspect_test_plan(task: Task, data: bytes) -> Plan:
    worktree = Path(task.worktree_path)
    requirements, _ = approved_test_inputs(task, worktree)
    trace = requirements_trace(requirements)
    assert trace is not None
    return parse_plan(data, {
        name: getattr(task, name) for name in (
            "task_id", "requirements_sha256", "requirements_approval_id", "design_sha256", "design_approval_id",
        )
    }, trace)


def test_bundle(task: Task, data: bytes) -> tuple[str | None, dict[str, bytes], dict | None]:
    plan = inspect_test_plan(task, data)
    if plan.waiting:
        return None, {}, None
    inputs = test_inputs(Path(task.worktree_path), task.base_commit, plan)
    automated = {key: case for key, case in plan.cases.items() if case.mode in ("red", "pass")}
    evidence = task.test_evidence
    if automated:
        inputs = capture_task_inputs(task, inputs)
        files = file_hashes(inputs)
        if (not evidence or evidence.get("policy") != EVIDENCE_POLICY
                or evidence.get("fingerprint") != bundle_digest(data, files, None)):
            raise WorkflowError("Run test-run against the current plan and tests before submitting.")
        outcomes = evidence.get("outcomes", {})
        if any(outcomes.get(key, {}).get("status") != ("red" if case.mode == "red" else "passed") for key, case in automated.items()):
            raise WorkflowError("pytest outcomes did not match the plan. Inspect test_evidence before submitting.")
    evidence = evidence if automated else None
    return bundle_digest(data, file_hashes(inputs), evidence), inputs, evidence


def capture_task_inputs(task: Task, inputs: dict[str, bytes]) -> dict[str, bytes]:
    return execution_inputs(Path(task.worktree_path), inputs, task_docs=f"docs/changes/{task.task_id}/")


def prepare_stage(task: Task) -> Task:
    assert task.worktree_path is not None
    changes = {f"{task.current_stage}_sha256": None}
    try:
        task_document(task, prepare=True)
        if task.current_stage == "design":
            approved_requirements(task, Path(task.worktree_path))
        elif task.current_stage == "test_design":
            approved_test_inputs(task, Path(task.worktree_path))
        elif task.current_stage == "development":
            development_inputs(task)
        elif task.current_stage == "acceptance":
            verify_approved_development(task)
    except (OSError, RequirementsError, WorkflowError) as exc:
        if task.current_stage == "acceptance" or (task.current_stage, task.status) == ("development", "not_implemented"):
            raise
        return replace(task, status="failed", error=str(exc), paused_from=None, **changes)
    return replace(task, status="running", error=None, paused_from=None, **changes)


@contextmanager
def task_lock(path: Path) -> Iterator[None]:
    if not path.is_file():
        raise WorkflowError(f"Task not found: {path.parent.name}")
    with path.with_name("state.lock").open("a+b") as stream:
        try:
            if os.name == "nt":
                import msvcrt

                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise WorkflowError(f"Task {path.parent.name} is being updated. Retry the command.") from exc
        yield  # Closing the handle releases the lock, including after process interruption.


def retired_approvals(task: Task) -> list[dict[str, object]]:
    history = task.approval_history.copy()
    if task.requirements_approval_id and task.requirements_sha256:
        history.append({
            "stage": "requirements", "approval_id": task.requirements_approval_id,
            "sha256": task.requirements_sha256,
        })
    if task.design_approval_id and task.design_sha256:
        assert task.requirements_approval_id and task.requirements_sha256
        history.append({
            "stage": "design", "approval_id": task.design_approval_id, "sha256": task.design_sha256,
            "requirements_approval_id": task.requirements_approval_id,
            "requirements_sha256": task.requirements_sha256,
        })
    if task.test_design_approval_id and task.test_design_sha256:
        history.append({
            "stage": "test_design", "approval_id": task.test_design_approval_id,
            "sha256": task.test_design_sha256, "design_approval_id": task.design_approval_id,
            "design_sha256": task.design_sha256,
        })
        if task.test_design_archive_sha256:
            history[-1]["archive_sha256"] = task.test_design_archive_sha256
    return history


def transition(task: Task, operation: str, approval: str | None, input_received: bool) -> Task:
    stage = task.current_stage
    if operation == "revise-development" and stage == "acceptance":
        worktree = Path(task.worktree_path)
        verify_worktree(PROJECT_ROOT, worktree, task.branch)
        retired = {
            "stage": "development", "approval_id": task.development_approval_id, "sha256": task.development_sha256,
            "test_design_sha256": task.test_design_sha256,
        }
        if task.development_archive_sha256 is not None:
            retired["archive_sha256"] = task.development_archive_sha256
        history = [*task.approval_history, retired]
        if task.acceptance_approval_id:
            history.append({
                "stage": "acceptance", "approval_id": task.acceptance_approval_id, "sha256": task.acceptance_sha256,
                "development_approval_id": task.development_approval_id,
                "development_archive_sha256": task.development_archive_sha256,
                "evidence": task.acceptance_evidence,
            })
        return prepare_stage(replace(
            task, current_stage="development", status="running",
            approval_history=history,
            development_sha256=None, development_approval_id=None, development_archive_sha256=None,
            development_evidence=None,
            acceptance_evidence=None, acceptance_sha256=None, acceptance_approval_id=None,
        ))
    if task.status == "awaiting_integration":
        raise WorkflowError("Acceptance is approved. Integration and cleanup require a separate manual review.")
    if operation == "revise-requirements" and stage in ("design", "test_design", "development"):
        return prepare_stage(replace(
            task, current_stage="requirements", requirements_completed=False, requirements_approval_id=None,
            requirements_revision_required=False, design_sha256=None, design_approval_id=None,
            approval_history=retired_approvals(task),
            test_design_sha256=None, test_design_approval_id=None, test_design_archive_sha256=None, test_evidence=None,
            development_sha256=None, development_evidence=None,
        ))
    if operation in ("revise-design", "revise-tests") and stage == "development":
        retired = retired_approvals(task)
        target = "design" if operation == "revise-design" else "test_design"
        return prepare_stage(replace(
            task, current_stage=target, approval_history=[*task.approval_history, *retired[-(2 if target == "design" else 1):]],
            design_approval_id=None if target == "design" else task.design_approval_id,
            test_design_sha256=None, test_design_approval_id=None, test_design_archive_sha256=None, test_evidence=None,
            development_sha256=None, development_evidence=None,
        ))
    if stage not in ACTIVE_STAGES:
        raise WorkflowError(f"Cannot {operation} during {task.current_stage}/{task.status}.")
    if stage == "design" and task.requirements_approval_id is None:
        raise WorkflowError("No approved Requirements snapshot. Use revise-requirements and reapprove.")
    if operation == "pause":
        return task if task.status == "paused" else replace(task, status="paused", paused_from=task.status)
    if operation == "resume":
        if task.status == "paused":
            task = replace(task, status=task.paused_from or "not_implemented", paused_from=None)
        return prepare_stage(task) if task.status == "running" else task
    if task.status == "paused":
        raise WorkflowError("Resume the paused task first.")
    if operation == "continue":
        if task.requirements_revision_required:
            raise WorkflowError("Design requires a Requirements change. Use revise-requirements and reapprove.")
        if task.status == "waiting_for_input" and not input_received:
            raise WorkflowError(f"Continue after the user answers the open {stage} questions (--with-input).")
        return prepare_stage(task)
    if stage == "development":
        return development_transition(task, operation, approval)
    if stage == "acceptance":
        return acceptance_transition(task, operation, approval)

    if operation not in ("submit", "approve"):
        raise WorkflowError(f"Cannot {operation} during {stage}/{task.status}.")
    expected_status = "running" if operation == "submit" else "waiting_for_approval"
    if task.status != expected_status:
        raise WorkflowError(f"Cannot {operation} while {stage} is {task.status}.")
    expected_digest = getattr(task, f"{stage}_sha256")
    if operation == "approve" and (approval is None or approval != expected_digest):
        raise WorkflowError(f"Approval must specify the SHA-256 shown for the submitted {stage}.")
    assert task.worktree_path is not None
    worktree = Path(task.worktree_path)
    draft = task_document(task)
    data = draft.read_bytes()
    requires_revision = False
    if stage == "requirements":
        ready = review_requirements(data) is not None
    elif stage == "design":
        _, functions = approved_requirements(task, worktree)
        assert task.requirements_sha256 is not None and task.requirements_approval_id is not None
        review = review_design(
            data, task_id=task.task_id, requirements_sha256=task.requirements_sha256,
            requirements_approval_id=task.requirements_approval_id, functions=functions,
        )
        ready, requires_revision = review.ready, review.requires_requirements_revision
    if stage == "test_design":
        digest, inputs, evidence = test_bundle(task, data)
    else:
        digest = sha256(data).hexdigest() if ready else None
    if operation == "submit":
        status = "waiting_for_approval" if digest else "waiting_for_input"
        return replace(
            task, status=status, requirements_revision_required=requires_revision, **{f"{stage}_sha256": digest},
        )
    if digest is None or digest != approval:
        raise WorkflowError(f"{stage.capitalize()} changed after submission. Continue and submit again before approval.")
    approval_id = uuid4().hex
    snapshot = snapshot_path(worktree, task.task_id, stage, approval_id)
    publish_snapshot(worktree, snapshot, data)
    if stage == "requirements":
        return prepare_stage(replace(
            task, current_stage="design", requirements_completed=True, requirements_approval_id=approval_id,
        ))
    if stage == "design":
        return prepare_stage(replace(task, current_stage="test_design", design_approval_id=approval_id))
    archive = bundle_archive(data, inputs, evidence)
    publish_snapshot(worktree, snapshot.with_suffix(".zip"), archive)
    return replace(
        task, current_stage="development", status="not_implemented",
        test_design_approval_id=approval_id, test_design_archive_sha256=sha256(archive).hexdigest(),
    )


def task_state_path(root: Path, task_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", task_id):
        raise WorkflowError("Specify the 32-character task ID returned by start.")
    return root / task_id / "state.json"


def design_input(root: Path, task_id: str) -> dict[str, object]:
    path = task_state_path(root, task_id)
    with task_lock(path):
        task = load(path, task_id)
        if (task.current_stage, task.status) != ("design", "running"):
            raise WorkflowError("Design input is available only during design/running.")
        if task.state_version == 0:
            raise WorkflowError("Continue or resume this legacy task to obtain a state version.")
        assert task.worktree_path is not None
        task_document(task, prepare=True)
        data, _ = approved_requirements(task, Path(task.worktree_path))
        return {**task.report(), "approved_requirements": data.decode("utf-8")}


def test_design_input(root: Path, task_id: str) -> dict[str, object]:
    path = task_state_path(root, task_id)
    with task_lock(path):
        task = load(path, task_id)
        if (task.current_stage, task.status) != ("test_design", "running"):
            raise WorkflowError("Test Design input is available only during test_design/running.")
        task_document(task, prepare=True)
        requirements, design = approved_test_inputs(task, Path(task.worktree_path))
        return {
            **task.report(), "approved_requirements": requirements.decode("utf-8"), "approved_design": design.decode("utf-8"),
        }


def run_test_design(task: Task) -> Task:
    if (task.current_stage, task.status) != ("test_design", "running"):
        raise WorkflowError("test-run requires test_design/running.")
    path = task_document(task)
    data = path.read_bytes()
    plan = inspect_test_plan(task, data)
    if plan.waiting or not any(case.mode in ("red", "pass") for case in plan.cases.values()):
        raise WorkflowError("test-run needs a complete plan with automated pytest cases.")
    worktree = Path(task.worktree_path)
    inputs = capture_task_inputs(task, test_inputs(worktree, task.base_commit, plan))
    files = file_hashes(inputs)
    evidence = run_captured_cases(inputs, plan.cases)
    if data != path.read_bytes() or files != file_hashes(
        capture_task_inputs(task, test_inputs(worktree, task.base_commit, plan))
    ):
        raise WorkflowError("Test plan or files changed during pytest; run again.")
    return replace(task, test_evidence={
        **evidence, "fingerprint": bundle_digest(data, files, None), "files": files,
        "trace": [{"fr": fr, "ac": ac, "case": case} for fr, ac, case in plan.trace],
    })


def run_captured_cases(inputs: dict[str, bytes], cases: dict) -> dict:
    with TemporaryDirectory(prefix="td-run-") as directory:
        copy = Path(directory)
        for name, contents in inputs.items():
            destination = copy / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as stream:
                stream.write(contents)
        evidence = run_cases(copy, cases)
        if any(checked_artifact(copy, copy / name).read_bytes() != contents for name, contents in inputs.items()):
            raise WorkflowError("Captured inputs changed inside the pytest run; no evidence recorded.")
    return evidence


def verified_test_bundle(
    task: Task, *, check_live: bool, allow_historical_policy: bool = False,
) -> tuple[bytes, Plan, dict[str, bytes], dict | None]:
    """Validate and return the approved bytes, never mutable working-copy inputs.

    Approval commits an archive, not a promise that an editor cannot change the
    checkout. Consumers must use the returned bytes after the live staleness
    check; status/continue retire an approval whose checkout has diverged.
    """
    assert task.worktree_path is not None
    worktree = Path(task.worktree_path)
    task_document(task)  # Validate the registered worktree even for archive-only checks.
    snapshot = task.approved_path("test_design")
    archive = task.test_design_archive()
    if snapshot is None or archive is None or task.test_design_archive_sha256 is None:
        raise WorkflowError("No verified Test Design archive. Submit for approval again.")
    data, inputs, evidence = read_bundle(worktree, archive, task.test_design_archive_sha256, "test-plan.md")
    if data != read_snapshot(worktree, snapshot, sha256(data).hexdigest()):
        raise WorkflowError("Approved test plan does not match the archive.")
    plan = inspect_test_plan(task, data)
    if plan.waiting or bundle_digest(data, file_hashes(inputs), evidence) != task.test_design_sha256:
        raise WorkflowError("Approved archive does not match the submitted bundle.")
    if any(case.mode in ("red", "pass") for case in plan.cases.values()) and (
        not isinstance(evidence, dict)
        or (evidence.get("policy") != EVIDENCE_POLICY and not allow_historical_policy)
    ):
        raise WorkflowError("Approved evidence uses an obsolete execution policy. Run and approve again.")
    if check_live:
        if task_document(task).read_bytes() != data or test_bundle(task, data)[0] != task.test_design_sha256:
            raise WorkflowError("Approved test artifacts no longer match the bundle.")
    return data, plan, inputs, evidence


def read_bundle(
    worktree: Path, archive: Path, digest: str, document: str,
) -> tuple[bytes, dict[str, bytes], dict | None]:
    contents = read_snapshot(worktree, archive, digest)
    try:
        with ZipFile(BytesIO(contents)) as bundle:
            names = bundle.namelist()
            if len(names) != len(set(names)) or not {document, "evidence.json"}.issubset(names):
                raise ValueError("Duplicate or missing archive entries")
            for name in names:
                if name not in (document, "evidence.json") and (
                    not name.startswith("files/") or not relative_input(name[6:])
                ):
                    raise ValueError("Invalid archive input path")
            data = bundle.read(document)
            evidence = json.loads(bundle.read("evidence.json"))
            if evidence is not None and not isinstance(evidence, dict):
                raise ValueError("Invalid archive evidence")
            inputs = {name[6:]: bundle.read(name) for name in names if name.startswith("files/")}
    except (BadZipFile, ValueError, KeyError, RuntimeError) as exc:
        stage = "Test Design" if document == "test-plan.md" else "Development"
        raise WorkflowError(f"Invalid {stage} archive: {exc}") from exc
    return data, inputs, evidence


def verify_approved_test_bundle(task: Task, *, check_live: bool = True) -> dict[str, bytes]:
    return verified_test_bundle(task, check_live=check_live)[2]


def development_inputs(
    task: Task, *, allow_historical_policy: bool = False,
) -> tuple[bytes, Plan, dict[str, bytes], dict | None]:
    worktree = Path(task.worktree_path)
    plan_data, plan, approved, evidence = verified_test_bundle(
        task, check_live=False, allow_historical_policy=allow_historical_policy,
    )
    if task_document(task).read_bytes() != plan_data:
        raise WorkflowError("Test plan changed. Use revise-tests and reapprove.")
    for stage, filename in (("requirements", "requirements.md"), ("design", "design.md")):
        expected = read_snapshot(worktree, task.approved_path(stage), getattr(task, f"{stage}_sha256"))
        if document_path(worktree, task.task_id, prepare=False, filename=filename).read_bytes() != expected:
            raise WorkflowError(f"{stage.capitalize()} draft differs from approval. Use revise-{stage} and reapprove.")
    def protected(name: str) -> bool:
        parts = name.casefold().split("/")
        return (
            name in plan.inputs or parts[0] in (*TEST_ROOTS, "data")
            or parts[-1] in PYTEST_CONFIG
        )

    live = test_inputs(worktree, task.base_commit, plan)
    captured = capture_task_inputs(task, live)
    automated = {key: case for key, case in plan.cases.items() if case.mode in ("red", "pass")}
    protected_live = captured if automated else live
    if {name: data for name, data in protected_live.items() if protected(name)} != {
        name: data for name, data in approved.items() if protected(name)
    }:
        raise WorkflowError("Approved test files, fixtures or configuration changed. Use revise-tests and reapprove.")
    if automated and (
        not evidence or evidence.get("fingerprint") != bundle_digest(plan_data, file_hashes(approved), None)
        or any(
            evidence.get("outcomes", {}).get(key, {}).get("status") != ("red" if case.mode == "red" else "passed")
            or evidence["outcomes"][key].get("node") != case.target
            for key, case in automated.items()
        )
    ):
        raise WorkflowError("Missing or invalid approved Red/Pass evidence. Use revise-tests and reapprove.")
    return plan_data, plan, captured, evidence


def development_input(root: Path, task_id: str) -> dict[str, object]:
    path = task_state_path(root, task_id)
    with task_lock(path):
        task = load(path, task_id)
        if (task.current_stage, task.status) != ("development", "running"):
            raise WorkflowError("Development input is available only during development/running.")
        plan_data, _, _, evidence = development_inputs(task)
        requirements, design = approved_test_inputs(task, Path(task.worktree_path))
        return {
            **task.report(),
            "approved_requirements": requirements.decode("utf-8"),
            "approved_design": design.decode("utf-8"), "approved_test_plan": plan_data.decode("utf-8"),
            "approved_test_evidence": evidence,
            "approved_test_evidence_scope": "test_design_archive",
        }


def run_development(task: Task) -> Task:
    if (task.current_stage, task.status) != ("development", "running"):
        raise WorkflowError("Development test-run requires development/running.")
    plan_data, plan, captured, _ = development_inputs(task)
    cases = {key: replace(case, mode="pass") for key, case in plan.cases.items() if case.mode in ("red", "pass")}
    if not cases:
        raise WorkflowError("No automated nodes in the approved plan. Document the approved alternative.")
    try:
        result = run_captured_cases(captured, cases)
    except (RequirementsError, OSError) as exc:
        return replace(task, status="failed", development_evidence=None, error=str(exc))
    live_plan, _, live_files, _ = development_inputs(task)
    if (plan_data, captured) != (live_plan, live_files):
        raise WorkflowError("Development files changed while pytest ran. Run again.")
    evidence = {**result, "scope": "implementation_snapshot",
                "fingerprint": bundle_digest(plan_data, file_hashes(captured), None)}
    passed = result["exit_code"] == 0 and all(outcome["status"] == "passed" for outcome in result["outcomes"].values())
    return replace(task, development_evidence=evidence, status="running" if passed else "failed",
                   error=None if passed else "Development tests failed. Inspect development_evidence and continue.")


def development_transition(task: Task, operation: str, approval: str | None) -> Task:
    if operation in ("block", "fail") and task.status == "running":
        return replace(task, status="waiting_for_input" if operation == "block" else "failed", error=approval)
    if (operation, task.status) not in (("submit", "running"), ("approve", "waiting_for_approval")):
        raise WorkflowError(f"Cannot {operation} during development/{task.status}.")
    data, plan, captured, _ = development_inputs(task)
    worktree = Path(task.worktree_path)
    record = document_path(worktree, task.task_id, prepare=False, filename="development.md").read_bytes()
    try:
        summary = record.decode("utf-8-sig")
    except UnicodeError as exc:
        raise WorkflowError("Development record must be UTF-8 Markdown.") from exc
    cases = {key: case for key, case in plan.cases.items() if case.mode in ("red", "pass")}
    evidence = task.development_evidence
    hashes = file_hashes(captured)
    if cases and (
        not evidence or evidence.get("fingerprint") != bundle_digest(data, hashes, None)
        or evidence.get("exit_code") != 0
        or any(evidence.get("outcomes", {}).get(key, {}).get("status") != "passed" for key in cases)
    ):
        raise WorkflowError("Run test-run and pass all approved automated cases on the current implementation.")
    sections = markdown_sections(summary, record_sections=("Trace",))
    if not sections.get("Summary", "").strip() or not sections.get("Verification", "").strip():
        raise WorkflowError("Development record requires Summary and Verification sections.")
    rows = set()
    for line in sections.get("Trace", "").splitlines():
        if not line.strip():
            continue
        row = re.fullmatch(r"- (FR-[0-9]{3}) / (AC-[0-9]{3}): \S.*", line.rstrip())
        if row is None or row.groups() in rows:
            raise WorkflowError("Use unique '- FR-001 / AC-001: implementation and verification' trace rows.")
        rows.add(row.groups())
    if rows != {(fr, ac) for fr, ac, _ in plan.trace}:
        raise WorkflowError("Development trace must cover all and only the approved FR/AC pairs.")
    evidence = {"test_design_sha256": task.test_design_sha256, "green": evidence}
    digest = bundle_digest(record, hashes, evidence)
    if operation == "submit":
        return replace(task, status="waiting_for_approval", development_sha256=digest)
    if digest != task.development_sha256 or approval != digest:
        raise WorkflowError("Development changed or the approval SHA-256 does not match. Submit again.")
    approval_id = uuid4().hex
    archive = bundle_archive(record, captured, evidence, document="development.md")
    path = snapshot_path(worktree, task.task_id, "development", approval_id).with_suffix(".zip")
    publish_snapshot(worktree, path, archive)
    return replace(
        task, current_stage="acceptance", status="not_implemented", development_approval_id=approval_id,
        development_archive_sha256=sha256(archive).hexdigest(),
    )


def verify_approved_development(
    task: Task, *, check_live: bool = True, allow_historical_policy: bool = False,
) -> dict[str, bytes]:
    """Return archive bytes only, even when the live worktree is also checked."""
    snapshot = task.approved_path("development")
    if snapshot is None or task.development_archive_sha256 is None:
        raise WorkflowError("No verifiable Development approval archive.")
    worktree = Path(task.worktree_path)
    verify_worktree(PROJECT_ROOT, worktree, task.branch)
    record, files, evidence = read_bundle(
        worktree, snapshot.with_suffix(".zip"), task.development_archive_sha256, "development.md",
    )
    if (
        evidence != {"test_design_sha256": task.test_design_sha256, "green": task.development_evidence}
        or bundle_digest(record, file_hashes(files), evidence) != task.development_sha256
    ):
        raise WorkflowError("Development archive does not match the approved digest.")
    if check_live:
        _, _, captured, _ = development_inputs(task, allow_historical_policy=allow_historical_policy)
        live_record = document_path(worktree, task.task_id, prepare=False, filename="development.md").read_bytes()
        if captured != files or live_record != record:
            raise WorkflowError("Worktree differs from approved Development archive.")
    return files


def acceptance_metadata(task: Task) -> dict[str, str]:
    return {name: getattr(task, name) for name in (
        "task_id", "requirements_sha256", "design_sha256", "test_design_sha256", "development_sha256",
    )}


def acceptance_input(root: Path, task_id: str) -> dict[str, object]:
    path = task_state_path(root, task_id)
    with task_lock(path):
        task = load(path, task_id)
        if task.current_stage != "acceptance" or task.status not in ("running", "failed", "waiting_for_input"):
            raise WorkflowError("Acceptance input requires an active Acceptance review.")
        verify_approved_development(task)
        requirements, design = approved_test_inputs(task, Path(task.worktree_path))
        plan_data, _, _, _ = verified_test_bundle(task, check_live=False)
        return {
            **task.report(), "approved_requirements": requirements.decode("utf-8"),
            "approved_design": design.decode("utf-8"), "approved_test_plan": plan_data.decode("utf-8"),
        }


def run_acceptance(task: Task) -> Task:
    if (task.current_stage, task.status) != ("acceptance", "running"):
        raise WorkflowError("Acceptance test-run requires acceptance/running.")
    captured = verify_approved_development(task)
    _, plan, _, _ = verified_test_bundle(task, check_live=False)
    cases = {key: replace(case, mode="pass") for key, case in plan.cases.items() if case.mode in ("red", "pass")}
    if not cases:
        raise WorkflowError("No automated nodes in the approved plan. Record the reviewer’s actual verification.")
    try:
        result = run_captured_cases(captured, cases)
    except (RequirementsError, OSError) as exc:
        return replace(task, status="failed", acceptance_evidence=None, error=str(exc))
    if captured != verify_approved_development(task):
        raise WorkflowError("Development files changed during Acceptance testing.")
    evidence = {**result, "development_archive_sha256": task.development_archive_sha256}
    passed = result["exit_code"] == 0 and all(result["outcomes"].get(key, {}).get("status") == "passed" for key in cases)
    return replace(task, acceptance_evidence=evidence, status="running" if passed else "failed",
                   error=None if passed else "Acceptance tests failed. Inspect acceptance_evidence.")


def acceptance_digest(task: Task, data: bytes) -> str:
    return bundle_digest(data, {}, {
        "development_archive_sha256": task.development_archive_sha256,
        "development_approval_id": task.development_approval_id,
        "acceptance_evidence": task.acceptance_evidence,
    })


def verify_approved_acceptance(task: Task) -> None:
    worktree = Path(task.worktree_path)
    snapshot = checked_artifact(worktree, task.approved_path("acceptance")).read_bytes()
    if acceptance_digest(task, snapshot) != task.acceptance_sha256:
        raise WorkflowError("Acceptance snapshot does not match the approved digest.")


def acceptance_transition(task: Task, operation: str, approval: str | None) -> Task:
    if operation in ("block", "fail") and task.status == "running":
        return replace(task, status="waiting_for_input" if operation == "block" else "failed", error=approval)
    if (operation, task.status) not in (
        ("submit", "running"), ("submit", "failed"), ("approve", "waiting_for_approval"),
    ):
        raise WorkflowError(f"Cannot {operation} during acceptance/{task.status}.")
    verify_approved_development(task)
    worktree = Path(task.worktree_path)
    data = document_path(worktree, task.task_id, prepare=False, filename="acceptance.md").read_bytes()
    _, plan, _, _ = verified_test_bundle(task, check_live=False)
    evidence = task.acceptance_evidence
    if evidence is not None and (
        evidence.get("development_archive_sha256") != task.development_archive_sha256
        or evidence.get("policy") != EVIDENCE_POLICY
    ):
        raise WorkflowError("Acceptance evidence is stale. Run test-run again.")
    conclusion = review_acceptance(data, acceptance_metadata(task), plan, evidence)
    digest = acceptance_digest(task, data)
    if operation == "submit":
        if conclusion in ("FAIL", "BLOCKED"):
            return replace(task, status="failed" if conclusion == "FAIL" else "waiting_for_input",
                           error=f"Acceptance report: {conclusion}. See acceptance.md and acceptance_evidence.")
        if task.status == "failed":
            raise WorkflowError("Resolve the Acceptance failure and continue before submitting a passing report.")
        return replace(task, status="waiting_for_approval", acceptance_sha256=digest)
    if digest != task.acceptance_sha256 or digest != approval or conclusion not in ("PASS", "PASS WITH RISKS"):
        raise WorkflowError("Acceptance changed or the approval SHA-256 does not match. Submit again.")
    approval_id = uuid4().hex
    publish_snapshot(worktree, snapshot_path(worktree, task.task_id, "acceptance", approval_id), data)
    return replace(task, status="awaiting_integration", acceptance_approval_id=approval_id)


def check_test_approval(task: Task) -> Task:
    if (task.current_stage, task.status) != ("development", "not_implemented"):
        return task
    try:
        verify_approved_test_bundle(task)
    except (OSError, GitReadError) as exc:
        raise WorkflowError(f"Could not check Test Design approval. Retry without changing the approval: {exc}") from exc
    except (RequirementsError, WorkflowError) as exc:
        return replace(
            task, current_stage="test_design", status="failed", error=f"Test approval invalidated: {exc}",
            test_design_sha256=None, test_design_approval_id=None, test_design_archive_sha256=None, test_evidence=None,
            approval_history=[*task.approval_history, retired_approvals(task)[-1]],
        )
    return task


def task_command(root: Path, operation: str, task_id: str, *, approval: str | None = None,
                 input_received: bool = False, expected_stage: str | None = None,
                 expected_version: int | None = None) -> Task:
    path = task_state_path(root, task_id)
    if operation == "status":
        task = load(path, task_id)
        if task.current_stage not in POST_TEST_DESIGN:
            return task
    with task_lock(path):
        task = load(path, task_id)
        legacy_revision = (
            operation == "revise-requirements"
            and (task.current_stage, task.status) == ("design", "not_implemented")
            and task.requirements_approval_id is None
        )
        if (operation in ("submit", "approve", "test-run", "block", "fail", "revise-requirements", "revise-design",
                          "revise-tests", "revise-development")
                or expected_stage is not None or expected_version is not None
                or (task.current_stage in POST_TEST_DESIGN and operation in ("pause", "resume", "continue"))) and (
            (task.state_version == 0 and not legacy_revision)
            or (expected_stage, expected_version) != (task.current_stage, task.state_version)
        ):
            raise WorkflowError(
                f"Stale {operation}: expected stage {expected_stage} version {expected_version}, "
                f"current stage {task.current_stage} version {task.state_version}. "
                "Restart from the appropriate workflow step instead of replaying this command."
            )
        if operation in ("status", "continue"):
            checked = check_test_approval(task)
            if checked != task:
                checked = replace(checked, state_version=task.state_version + 1)
                save(path, checked)
                return checked
            if operation == "status":
                if task.current_stage == "acceptance":
                    try:
                        verify_approved_development(task, allow_historical_policy=True)
                        if task.status == "awaiting_integration":
                            verify_approved_acceptance(task)
                    except (WorkflowError, RequirementsError) as exc:
                        if isinstance(exc, GitReadError):
                            raise
                        if task.status == "awaiting_integration":
                            raise WorkflowError(
                                f"{exc} Do not integrate. Restore the approved version or run "
                                f"revise-development {task_id} --stage acceptance --version {task.state_version}."
                            ) from exc
                        raise WorkflowError(
                            f"{exc} Restore the approved version or request a new review with "
                            f"revise-development {task_id} --stage acceptance --version {task.state_version}."
                        ) from exc
                return task
        if operation == "test-run":
            execute = {"development": run_development, "acceptance": run_acceptance}.get(task.current_stage, run_test_design)
            updated = execute(task)
        else:
            updated = transition(task, operation, approval, input_received)
        if updated != task or (operation in ("continue", "resume") and updated.status == "running"):
            updated = replace(updated, state_version=task.state_version + 1)
            if operation == "approve" and task.current_stage == "test_design":
                verify_approved_test_bundle(updated)
                save(path, updated, verify_test_approval=True)
            elif operation == "approve" and task.current_stage == "development":
                verify_approved_development(updated, check_live=True)
                save(path, updated, verify_development_approval=True)
            elif operation == "approve" and task.current_stage == "acceptance":
                verify_approved_development(updated)
                save(path, updated, verify_acceptance_approval=True)
            else:
                save(path, updated)
        return updated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    commands.add_parser("start", help="create a task worktree and record its state").add_argument("task")
    for operation in ("status", "design-input", "test-design-input", "development-input", "acceptance-input"):
        commands.add_parser(operation).add_argument("task_id")
    reopen = commands.add_parser("revise-development", help="reopen approved Development for fresh verification")
    reopen.add_argument("task_id")
    reopen.add_argument("--stage", required=True, choices=("acceptance",))
    reopen.add_argument("--version", required=True, type=int)
    for operation in ("pause", "resume", "revise-requirements", "revise-design", "revise-tests", "block", "fail"):
        command = commands.add_parser(operation)
        command.add_argument("task_id")
        version_required = operation not in ("pause", "resume")
        command.add_argument("--stage", choices=ACTIVE_STAGES, required=version_required)
        command.add_argument("--version", type=int, required=version_required)
        if operation in ("block", "fail"):
            command.add_argument("reason")
    continuation = commands.add_parser("continue", help="continue analysis in the existing task Worktree")
    continuation.add_argument("task_id")
    continuation.add_argument("--with-input", action="store_true", help="the user answered the open questions")
    continuation.add_argument("--stage", choices=ACTIVE_STAGES)
    continuation.add_argument("--version", type=int)
    submit = commands.add_parser("submit", help="submit a draft from the specified stage and version")
    approve = commands.add_parser("approve", help="approve the exact submitted stage and version")
    test_run = commands.add_parser("test-run", help="run and record the current stage's offline pytest cases")
    for command in (submit, approve, test_run):
        command.add_argument("task_id")
        command.add_argument("--stage", required=True, choices=ACTIVE_STAGES)
        command.add_argument("--version", required=True, type=int)
    approve.add_argument("sha256")
    args = parser.parse_args()

    try:
        root = state_root()
        if args.operation == "design-input":
            print(json.dumps(design_input(root, args.task_id), indent=2))
            return 0
        if args.operation == "test-design-input":
            print(json.dumps(test_design_input(root, args.task_id), indent=2))
            return 0
        if args.operation == "development-input":
            print(json.dumps(development_input(root, args.task_id), indent=2))
            return 0
        if args.operation == "acceptance-input":
            print(json.dumps(acceptance_input(root, args.task_id), indent=2))
            return 0
        if args.operation == "start":
            task = start(root, args.task)
        else:
            task = task_command(
                root, args.operation, args.task_id,
                approval=getattr(args, "sha256", getattr(args, "reason", None)),
                input_received=getattr(args, "with_input", False),
                expected_stage=getattr(args, "stage", None), expected_version=getattr(args, "version", None),
            )
    except (OSError, WorkflowError, RequirementsError) as exc:
        print(f"dev-workflow: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(task.report(), indent=2))
    return int(task.status == "failed" and (
        args.operation in ("start", "continue", "resume", "approve", "revise-requirements", "revise-development", "fail")
        or (args.operation == "test-run" and task.current_stage in POST_TEST_DESIGN)
        or (args.operation == "submit" and task.current_stage == "acceptance")
    ))


if __name__ == "__main__":
    raise SystemExit(main())
