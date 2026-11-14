"""Validate a task worktree and the structure of its requirements artifact."""

import os
from pathlib import Path
import re
import stat
import subprocess


class RequirementsError(Exception):
    pass


class GitReadError(RequirementsError):
    pass


QUESTIONS = "待确认问题"
FUNCTIONS = "功能需求"
ACCEPTANCE = "验收标准"
TRACE = "需求与验收标准追踪关系"
REQUIRED_SECTIONS = (
    "需求背景与业务目标", "当前系统行为", "需求范围与非目标", "角色及业务场景",
    FUNCTIONS, "业务规则与约束", "异常及边界场景", ACCEPTANCE, QUESTIONS, TRACE,
)
FR_ID = r"FR-(?!000)[0-9]{3}"
AC_ID = r"AC-(?!000)[0-9]{3}"


def read_git(path: Path, *args: str) -> bytes:
    env = os.environ.copy()
    for key in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(key, None)
    try:
        return subprocess.run(
            ["git", "-C", str(path), *args],
            stdin=subprocess.DEVNULL, capture_output=True, env=env, timeout=30, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitReadError("Could not verify the task Worktree registration with Git.") from exc


def verify_worktree(project: Path, path: Path, branch: str) -> None:
    if not path.is_dir() or path.resolve() != path or not (path / ".git").is_file():
        raise RequirementsError(f"Task Worktree is missing or invalid: {path}")
    listing = read_git(project, "worktree", "list", "--porcelain", "-z")
    expected_branch = f"refs/heads/{branch}".encode("ascii")
    for record in listing.split(b"\0\0")[1:]:  # Git lists the main Worktree first.
        first, _, remainder = record.partition(b"\0")
        if first.startswith(b"worktree ") and Path(os.fsdecode(first[9:])) == path:
            fields = remainder.split(b"\0")
            if b"branch " + expected_branch in fields and not any(field.startswith(b"prunable") for field in fields):
                rev_parse = ("rev-parse", "--path-format=absolute", "--git-common-dir")
                source_common = read_git(project, *rev_parse).rstrip(b"\r\n")
                checkout = read_git(path, *rev_parse, "--show-toplevel", "--symbolic-full-name", "HEAD")
                if checkout.splitlines() == [source_common, os.fsencode(path.as_posix()), expected_branch]:
                    return
            break
    raise RequirementsError(f"The task path and branch do not match a registered linked Worktree: {path}")


def check_path(path: Path, *, directory: bool, missing_ok: bool = False) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        if missing_ok:
            return
        raise RequirementsError(f"Requirements path is missing: {path}") from exc
    reparse = getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    valid = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    if reparse or not valid:
        raise RequirementsError(f"Requirements path must use ordinary files and directories: {path}")


def artifact_path(worktree: Path, task_id: str) -> Path:
    return worktree / "docs" / "changes" / task_id / "requirements.md"


def document_path(worktree: Path, task_id: str, *, prepare: bool, filename: str = "requirements.md") -> Path:
    path = artifact_path(worktree, task_id).with_name(filename)
    return checked_artifact(worktree, path, prepare=prepare)


def checked_artifact(worktree: Path, path: Path, *, prepare: bool = False) -> Path:
    parent = worktree
    for name in path.parent.relative_to(worktree).parts:
        parent /= name
        if prepare:
            parent.mkdir(exist_ok=True)
        check_path(parent, directory=True)
    check_path(path, directory=False, missing_ok=prepare)
    return path


def markdown_sections(text: str, *, record_sections: tuple[str, ...] = ()) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    heading = None
    fence = ""
    for line in text.splitlines():
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            if marker and marker[1].startswith(fence) and not marker[2].strip():
                fence = ""
        elif marker:
            if heading in record_sections:
                raise RequirementsError(f"Fenced blocks are not valid records in {heading}.")
            if marker[1].startswith("`") and "`" in marker[2]:
                raise RequirementsError("Invalid Markdown fence opener.")
            fence = marker[1]
        elif "<!--" in line or "-->" in line or re.match(r"^ {0,3}<(?:[!?]|/?[A-Za-z][\w-]*(?:\s|/?>))", line):
            raise RequirementsError("Raw HTML blocks and comments are not supported in review documents.")
        elif title := re.fullmatch(r" {0,3}##[ \t]+(\S.*?)\s*", line):
            heading = re.sub(r"[ \t]+#+$", "", title[1])
            if heading in sections:
                raise RequirementsError(f"Duplicate requirements heading: {heading}")
            sections[heading] = []
        elif heading is not None:
            if heading in record_sections and line.strip() and re.match(r"^(?: {4}| *\t)", line):
                raise RequirementsError(f"Indented code is not a valid record in {heading}.")
            sections[heading].append(line)
    if fence:
        raise RequirementsError("Unclosed Markdown code fence.")
    return {title: "\n".join(lines).strip("\n") for title, lines in sections.items()}


def pending_question_ids(sections: dict[str, str], *, allow_revision: bool = False) -> set[str]:
    questions = sections.get(QUESTIONS, "")
    if not questions.strip():
        raise RequirementsError(f"Fill in the {QUESTIONS} section with questions or 无.")
    if questions.strip() == "无":
        return set()

    prefix = r"(?:Q|RQ)" if allow_revision else "Q"
    identifiers: set[str] = set()
    for line in questions.splitlines():
        if not line.strip():
            continue
        record = re.fullmatch(rf"- ({prefix}-(?!000)[0-9]{{3}})[ \t]+\S.*", line.rstrip())
        if not record or record[1] in identifiers:
            label = "Q-001 or RQ-001" if allow_revision else "Q-001"
            raise RequirementsError(f"Use unique - {label} records for open questions, or only 无.")
        identifiers.add(record[1])
    return identifiers


def declared_ids(section: str, pattern: str) -> set[str]:
    prefix = pattern[:2]
    identifiers: set[str] = set()
    for line in section.splitlines():
        record = re.fullmatch(rf"- ({pattern})[ \t]+\S.*", line.rstrip())
        if record:
            if record[1] in identifiers:
                raise RequirementsError(f"Duplicate identifier: {record[1]}")
            identifiers.add(record[1])
        elif re.match(r"(?:[-+*]|[0-9]+[.)])\s", line.lstrip()) or re.search(
            rf"\b{prefix}\s*(?:[-‐‑–—]|[0-9])", line, re.IGNORECASE,
        ):
            raise RequirementsError(f"Invalid {prefix} record: {line}")
    if not identifiers:
        raise RequirementsError(f"Provide unique, nonempty {pattern[:2]} identifiers.")
    return identifiers


def review_requirements(data: bytes) -> set[str] | None:
    """Return traceable FR identifiers, or None while questions are open."""
    trace = requirements_trace(data)
    return set(trace) if trace is not None else None


def requirements_trace(data: bytes) -> dict[str, set[str]] | None:
    try:
        sections = markdown_sections(
            data.decode("utf-8-sig"), record_sections=(FUNCTIONS, ACCEPTANCE, TRACE, QUESTIONS),
        )
    except UnicodeError as exc:
        raise RequirementsError("Requirements must be saved as UTF-8 Markdown.") from exc

    if pending_question_ids(sections):
        return None

    missing = [heading for heading in REQUIRED_SECTIONS if not sections.get(heading, "").strip()]
    if missing:
        raise RequirementsError(f"Missing or empty requirements sections: {', '.join(missing)}")
    functions = declared_ids(sections[FUNCTIONS], FR_ID)
    criteria = declared_ids(sections[ACCEPTANCE], AC_ID)
    trace: dict[str, set[str]] = {}
    traced_criteria: set[str] = set()
    for line in sections[TRACE].splitlines():
        if not line.strip():
            continue
        row = re.fullmatch(rf"- ({FR_ID}): ({AC_ID}(?:,[ \t]*{AC_ID})*)", line.rstrip())
        if not row or row[1] in trace:
            raise RequirementsError("Use one trace row per FR, for example - FR-001: AC-001, AC-002.")
        row_criteria = re.findall(AC_ID, row[2])
        if len(row_criteria) != len(set(row_criteria)):
            raise RequirementsError(f"Duplicate acceptance reference for {row[1]}.")
        trace[row[1]] = set(row_criteria)
        traced_criteria.update(row_criteria)
    if trace.keys() != functions or traced_criteria != criteria:
        raise RequirementsError("The trace section must cover all and only the declared FR and AC identifiers.")
    return trace
