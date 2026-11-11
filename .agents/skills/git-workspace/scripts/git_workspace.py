"""Create an isolated Git task worktree or preview its manual removal."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from time import sleep
from uuid import uuid4


BASE_BRANCH_ENV = "TV_AUTOMATION_BASE_BRANCH"
DEFAULT_BASE_BRANCH = "master"
BRANCH_PREFIX = "codex/git-workspace/"
TASK_PATTERN = r"[a-z0-9]+(?:-[a-z0-9]+)*"
WORKTREE_PATTERN = rf"({TASK_PATTERN})-[0-9a-f]{{32}}"
GIT_TIMEOUT = 120


class WorkspaceError(Exception):
    pass


class GitCommandError(WorkspaceError):
    def __init__(self, message: str, stderr: bytes) -> None:
        super().__init__(message)
        self.stderr = stderr


def git(directory: Path, *args: str, error: str) -> bytes:
    # Git's local environment overrides can redirect even commands using -C.
    env = os.environ.copy()
    for key in (
        "GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
        "GIT_NAMESPACE", "GIT_QUARANTINE_PATH",
    ):
        env.pop(key, None)
    env.update(LC_ALL="C", GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    try:
        result = subprocess.run(
            ["git", "-C", str(directory), "-c", "core.hooksPath=/dev/null", *args],
            capture_output=True, stdin=subprocess.DEVNULL, env=env, timeout=GIT_TIMEOUT,
        )
    except FileNotFoundError as exc:
        raise WorkspaceError("Git CLI is not available on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise WorkspaceError(f"{error} Git timed out after {GIT_TIMEOUT} seconds.") from exc
    if result.returncode:
        # Git stderr can include the remote URL and its credentials.
        raise GitCommandError(f"{error} Git exit code: {result.returncode}.", result.stderr)
    return result.stdout


def worktrees(directory: Path) -> list[dict[bytes, bytes]]:
    listing = git(directory, "worktree", "list", "--porcelain", "-z", error="Cannot list worktrees.")
    entries = []
    for record in listing.split(b"\0\0"):
        if record:
            fields = (field.partition(b" ") for field in record.split(b"\0"))
            entries.append({key: value for key, _, value in fields})
    return entries


def git_path(output: bytes) -> Path:
    return Path(os.fsdecode(output.rstrip(b"\r\n"))).resolve()


def require_directory(path: Path) -> None:
    info = path.lstat()
    reparse_point = getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    if reparse_point or not stat.S_ISDIR(info.st_mode):
        raise WorkspaceError(f"Expected an ordinary directory: {path}")


def fetch_baseline(directory: Path) -> str:
    branch = os.environ.get(BASE_BRANCH_ENV, DEFAULT_BASE_BRANCH)
    invalid_branch = f"{BASE_BRANCH_ENV} must be a Git branch name such as master or main."
    if not branch or branch.startswith("-") or branch.casefold() == "head":
        raise WorkspaceError(invalid_branch)
    source_ref = f"refs/heads/{branch}"
    tracking_ref = f"refs/remotes/origin/{branch}"
    git(directory, "check-ref-format", source_ref, error=invalid_branch)

    # Fetch only the configured branch, regardless of remote fetch and pruning settings.
    for attempt in range(3):
        try:
            git(directory, "fetch", "--no-tags", "--no-prune", "--no-prune-tags",
                "--no-recurse-submodules", "--no-auto-maintenance", "--no-write-fetch-head",
                "--refmap=", "origin", f"+{source_ref}:{tracking_ref}",
                error=f"Cannot fetch origin {branch}. Check the remote and credentials.")
            break
        except GitCommandError as exc:
            if os.fsencode(f"couldn't find remote ref {source_ref}") in exc.stderr:
                raise WorkspaceError(
                    f"origin has no {branch} branch. No task workspace was created. "
                    f"Set {BASE_BRANCH_ENV} to an existing branch to use a different baseline."
                ) from exc
            # Another task may have advanced the shared ref during this fetch.
            if os.fsencode(f"cannot lock ref '{tracking_ref}'") not in exc.stderr:
                raise
            if attempt == 2:
                raise WorkspaceError(f"Cannot update origin/{branch} because its ref is locked.") from exc
            sleep(0.1 * (attempt + 1))
    return git(directory, "rev-parse", "--verify", f"{tracking_ref}^{{commit}}",
               error=f"Cannot resolve origin/{branch} to a commit.").strip().decode("ascii")


def create(directory: Path, storage: Path, task: str) -> dict[str, str]:
    if len(task) > 40 or not re.fullmatch(TASK_PATTERN, task):
        raise WorkspaceError("Use a task slug of 1–40 lowercase ASCII letters, digits and single hyphens.")

    baseline = fetch_baseline(directory)
    workspace_id = f"{task}-{uuid4().hex}"
    path = storage / workspace_id
    branch = BRANCH_PREFIX + workspace_id
    storage.mkdir(exist_ok=True)
    require_directory(storage)
    path.mkdir()  # Reserve an empty path exclusively. Git add -b refuses existing branches.
    git(directory, "worktree", "add", "-b", branch, "--", str(path), baseline,
        error=f"Cannot create worktree. Inspect path {path} and branch {branch} for partial results.")
    return {"worktree_path": str(path), "branch": branch, "base_commit": baseline}


def preview_cleanup(directory: Path, main: Path, storage: Path,
                    entries: list[dict[bytes, bytes]], path: Path) -> dict[str, object]:
    match = re.fullmatch(WORKTREE_PATTERN, path.name)
    if not path.is_absolute() or path.parent != storage or not match or len(match[1]) > 40:
        raise WorkspaceError(f"Specify the absolute path of a task worktree directly under {storage}.")

    require_directory(storage)
    require_directory(path)
    branch = BRANCH_PREFIX + path.name
    entry = next((item for item in entries[1:] if Path(os.fsdecode(item[b"worktree"])) == path), None)
    if not entry or entry.get(b"branch") != f"refs/heads/{branch}".encode("ascii"):
        raise WorkspaceError("The path and task branch do not match a registered linked worktree.")
    if b"locked" in entry or b"prunable" in entry:
        raise WorkspaceError("The worktree is locked or prunable. Inspect it manually.")

    common_dir = ("rev-parse", "--path-format=absolute", "--git-common-dir")
    target_common = git_path(git(path, *common_dir, error="Cannot identify the task repository."))
    source_common = git_path(git(directory, *common_dir, error="Cannot identify the source repository."))
    target_root = git_path(git(path, "rev-parse", "--show-toplevel", error="Cannot identify the task root."))
    if target_common != source_common or target_root != path:
        raise WorkspaceError("The target directory does not match the registered worktree.")

    index = git(path, "ls-files", "-v", "-z", error="Cannot inspect the worktree index.")
    if any(record.startswith(b"S ") or record[:1].islower() for record in index.split(b"\0")):
        raise WorkspaceError("The index contains skip-worktree or assume-unchanged entries. Inspect it manually.")

    changes = git(path, "-c", "core.fsmonitor=false", "status", "--porcelain=v1",
                  "--untracked-files=all", "--ignored=matching", "--ignore-submodules=none",
                  error="Cannot check the worktree for local files.")
    if changes:
        raise WorkspaceError("The worktree contains local changes, untracked files or ignored files.")
    return {
        "worktree_path": str(path),
        "branch": branch,
        "remove_argv": ["git", "-C", str(main), "worktree", "remove", "--", str(path)],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("create", help="fetch the configured origin branch and create a task worktree").add_argument("task")
    commands.add_parser("cleanup", help="preview manual removal of a clean task worktree").add_argument(
        "worktree", type=Path,
    )
    args = parser.parse_args()

    try:
        directory = git_path(git(Path.cwd(), "rev-parse", "--show-toplevel", error="Run inside a Git worktree."))
        entries = worktrees(directory)
        main_worktree = git_path(entries[0][b"worktree"])
        storage = main_worktree.with_name(f"{main_worktree.name}.worktrees")
        if args.command == "create":
            if any(storage.is_relative_to(git_path(entry[b"worktree"])) for entry in entries):
                raise WorkspaceError("The task storage directory would be inside an existing worktree.")
            result = create(directory, storage, args.task)
        else:
            result = preview_cleanup(directory, main_worktree, storage, entries, args.worktree)
    except (WorkspaceError, OSError) as exc:
        print(f"git-workspace: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
