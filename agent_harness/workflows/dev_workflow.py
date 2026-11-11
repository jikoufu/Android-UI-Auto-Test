"""Orchestrate Git Workspace, Requirements and Design."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import NamedTemporaryFile
from typing import Iterator
from uuid import uuid4

if __package__:
    from .approved_artifacts import publish_snapshot, read_snapshot, snapshot_path
    from .design_document import review_design
    from .requirements_document import RequirementsError, artifact_path, document_path, review_requirements, verify_worktree
else:
    from approved_artifacts import publish_snapshot, read_snapshot, snapshot_path
    from design_document import review_design
    from requirements_document import RequirementsError, artifact_path, document_path, review_requirements, verify_worktree


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GIT_WORKSPACE = Path(".agents/skills/git-workspace/scripts/git_workspace.py")
WORKSPACE_TIMEOUT = 900
STAGES = (
    "git_workspace", "requirements", "design", "test_design", "development", "acceptance",
    "integration_cleanup",
)
ACTIVE_STAGES = ("requirements", "design")
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
    approval_history: list[dict[str, str]] = field(default_factory=list)

    def requirements_path(self) -> Path:
        assert self.worktree_path is not None
        return artifact_path(Path(self.worktree_path), self.task_id)

    def approved_path(self, stage: str) -> Path | None:
        approval_id = self.requirements_approval_id if stage == "requirements" else self.design_approval_id
        if approval_id is None or self.worktree_path is None:
            return None
        return snapshot_path(Path(self.worktree_path), self.task_id, stage, approval_id)

    def report(self) -> dict[str, object]:
        workspace_ready = self.current_stage != "git_workspace"
        stages = dict.fromkeys(STAGES, "not_implemented")
        stages["git_workspace"] = "completed" if workspace_ready else self.status
        if workspace_ready:
            stages["requirements"] = "completed" if self.requirements_completed else self.status
        if self.current_stage in ("design", "test_design"):
            stages["design"] = "completed" if self.current_stage == "test_design" else self.status
        requirements_snapshot = self.approved_path("requirements")
        design_snapshot = self.approved_path("design")
        return {
            **asdict(self),
            "stages": stages,
            "requirements_path": str(self.requirements_path()) if workspace_ready else None,
            "design_path": str(self.requirements_path().with_name("design.md")) if self.requirements_completed else None,
            "requirements_snapshot": str(requirements_snapshot) if requirements_snapshot is not None else None,
            "design_snapshot": str(design_snapshot) if design_snapshot is not None else None,
            "next_stage": self.current_stage if workspace_ready and self.status != "failed" else None,
        }


def state_root() -> Path:
    runtime = os.environ.get("TV_AUTOMATION_RUNTIME_DIR") or "runtime"
    return (PROJECT_ROOT / runtime).resolve() / "state" / "dev-workflow"


def save(path: Path, task: Task) -> None:
    # Replace a complete record so readers see either the old or the new state.
    stream = NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False)
    temporary = Path(stream.name)
    try:
        with stream:
            json.dump(asdict(task), stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
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
        task = Task(**json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError as exc:
        raise WorkflowError(f"Task not found: {task_id}") from exc
    except (ValueError, TypeError) as exc:
        raise WorkflowError(f"Invalid task record: {path}") from exc

    valid_statuses = {
        "git_workspace": ("running", "failed"),
        "requirements": (*DRAFT_STATUSES, "paused"),
        "design": (*DRAFT_STATUSES, "paused"),
        "test_design": ("not_implemented",),
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
    if task.requirements_completed is not (task.current_stage in ("design", "test_design")):
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
        ("design", task.current_stage == "test_design", task.design_sha256, task.design_approval_id),
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
    if task.current_stage == "test_design" and task.design_approval_id is None:
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
    return document_path(worktree, task.task_id, prepare=prepare, filename=f"{task.current_stage}.md")


def approved_requirements(task: Task, worktree: Path) -> tuple[bytes, set[str]]:
    snapshot = task.approved_path("requirements")
    if not task.requirements_completed or snapshot is None or task.requirements_sha256 is None:
        raise WorkflowError("No approved Requirements snapshot. Use revise-requirements and reapprove.")
    data = read_snapshot(worktree, snapshot, task.requirements_sha256)
    functions = review_requirements(data)
    if functions is None:
        raise WorkflowError("The approved Requirements snapshot still contains open questions.")
    return data, functions


def prepare_stage(task: Task) -> Task:
    assert task.worktree_path is not None
    changes = {f"{task.current_stage}_sha256": None}
    try:
        task_document(task, prepare=True)
        if task.current_stage == "design":
            approved_requirements(task, Path(task.worktree_path))
    except (OSError, RequirementsError, WorkflowError) as exc:
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


def retired_approvals(task: Task) -> list[dict[str, str]]:
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
    return history


def transition(task: Task, operation: str, approval: str | None, input_received: bool) -> Task:
    stage = task.current_stage
    if operation == "revise-requirements" and stage in ("design", "test_design"):
        return prepare_stage(replace(
            task, current_stage="requirements", requirements_completed=False, requirements_approval_id=None,
            requirements_revision_required=False, design_sha256=None, design_approval_id=None,
            approval_history=retired_approvals(task),
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

    if operation not in ("submit", "approve"):
        raise WorkflowError(f"Cannot {operation} during {stage}/{task.status}.")
    expected_status = "running" if operation == "submit" else "waiting_for_approval"
    if task.status != expected_status:
        raise WorkflowError(f"Cannot {operation} while {stage} is {task.status}.")
    expected_digest = task.requirements_sha256 if stage == "requirements" else task.design_sha256
    if operation == "approve" and (approval is None or approval != expected_digest):
        raise WorkflowError(f"Approval must specify the SHA-256 shown for the submitted {stage}.")
    assert task.worktree_path is not None
    worktree = Path(task.worktree_path)
    data = task_document(task).read_bytes()
    requires_revision = False
    if stage == "requirements":
        ready = review_requirements(data) is not None
    else:
        _, functions = approved_requirements(task, worktree)
        assert task.requirements_sha256 is not None and task.requirements_approval_id is not None
        review = review_design(
            data, task_id=task.task_id, requirements_sha256=task.requirements_sha256,
            requirements_approval_id=task.requirements_approval_id, functions=functions,
        )
        ready, requires_revision = review.ready, review.requires_requirements_revision
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
    return replace(task, current_stage="test_design", status="not_implemented", design_approval_id=approval_id)


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


def task_command(root: Path, operation: str, task_id: str, *, approval: str | None = None,
                 input_received: bool = False, expected_stage: str | None = None,
                 expected_version: int | None = None) -> Task:
    path = task_state_path(root, task_id)
    if operation == "status":
        return load(path, task_id)
    with task_lock(path):
        task = load(path, task_id)
        if operation in ("submit", "approve") and (
            task.state_version == 0
            or (expected_stage, expected_version) != (task.current_stage, task.state_version)
        ):
            raise WorkflowError(
                f"Stale {operation}: expected stage {expected_stage} version {expected_version}, "
                f"current stage {task.current_stage} version {task.state_version}. "
                "Restart from the appropriate workflow step instead of replaying this command."
            )
        updated = transition(task, operation, approval, input_received)
        if updated != task or (operation in ("continue", "resume") and updated.status == "running"):
            updated = replace(updated, state_version=task.state_version + 1)
            save(path, updated)
        return updated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    commands.add_parser("start", help="create a task worktree and record its state").add_argument("task")
    for operation in ("status", "pause", "resume", "design-input", "revise-requirements"):
        commands.add_parser(operation).add_argument("task_id")
    continuation = commands.add_parser("continue", help="continue analysis in the existing task Worktree")
    continuation.add_argument("task_id")
    continuation.add_argument("--with-input", action="store_true", help="the user answered the open questions")
    submit = commands.add_parser("submit", help="submit a draft from the specified stage and version")
    approve = commands.add_parser("approve", help="approve the exact submitted stage and version")
    for command in (submit, approve):
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
        if args.operation == "start":
            task = start(root, args.task)
        else:
            task = task_command(
                root, args.operation, args.task_id, approval=getattr(args, "sha256", None),
                input_received=getattr(args, "with_input", False),
                expected_stage=getattr(args, "stage", None), expected_version=getattr(args, "version", None),
            )
    except (OSError, WorkflowError, RequirementsError) as exc:
        print(f"dev-workflow: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(task.report(), indent=2))
    return int(task.status == "failed" and args.operation in ("start", "continue", "resume", "approve", "revise-requirements"))


if __name__ == "__main__":
    raise SystemExit(main())
