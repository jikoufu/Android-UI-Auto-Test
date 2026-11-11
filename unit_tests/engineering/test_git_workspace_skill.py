"""Integration checks against temporary repositories and a local bare remote."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / ".agents/skills/git-workspace/scripts/git_workspace.py"


@pytest.fixture
def repositories(tmp_path, monkeypatch):
    for key in os.environ:
        if key.startswith("GIT_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global-config"))
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
