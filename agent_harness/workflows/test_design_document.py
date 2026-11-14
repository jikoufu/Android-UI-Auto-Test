"""Validate a test plan and fingerprint the files it proposes for approval."""

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import re
from zipfile import ZIP_DEFLATED, ZipFile

if __package__:
    from .requirements_document import (
        AC_ID, FR_ID, QUESTIONS, RequirementsError, checked_artifact,
        markdown_sections, pending_question_ids, read_git,
    )
else:
    from requirements_document import (
        AC_ID, FR_ID, QUESTIONS, RequirementsError, checked_artifact,
        markdown_sections, pending_question_ids, read_git,
    )

METADATA = "文档元数据"
TRACE = "FR / AC / 测试追踪矩阵"
INPUTS = "测试输入文件"
REQUIRED = (
    METADATA, "测试目标与范围", "测试层级及选择理由", "测试场景和用例",
    "测试数据与环境依赖", "Mock / Fixture 设计", TRACE, "自动化与人工测试边界",
    "Red 测试执行结果", "已知限制和风险", QUESTIONS,
)
CASE_ID = r"TC-(?!000)[0-9]{3}"
TEST_ROOTS = ("tests", "unit_tests")
OFFLINE_TEST_ROOT = ("unit_tests", "test_design")


@dataclass(frozen=True)
class PlanCase:
    mode: str
    target: str
    expectation: str


@dataclass(frozen=True)
class Plan:
    cases: dict[str, PlanCase]
    trace: tuple[tuple[str, str, str], ...] = ()
    waiting: bool = False
    inputs: tuple[str, ...] = ()


def parse_plan(data: bytes, metadata: dict[str, str], trace: dict[str, set[str]]) -> Plan:
    try:
        sections = markdown_sections(data.decode("utf-8-sig"), record_sections=(METADATA, TRACE, QUESTIONS, INPUTS))
    except UnicodeError as exc:
        raise RequirementsError("Test plan must be UTF-8 Markdown.") from exc
    actual = {}
    for line in sections.get(METADATA, "").splitlines():
        if not line.strip():
            continue
        item = re.fullmatch(r"- ([a-z0-9_]+): (\S+)", line.rstrip())
        if not item or item[1] in actual:
            raise RequirementsError("Use unique - key: value entries for test plan metadata.")
        actual[item[1]] = item[2]
    version = actual.pop("test_plan_version", "")
    if actual != metadata or not re.fullmatch(r"[1-9][0-9]*", version):
        raise RequirementsError("Test plan metadata does not match the current approvals or version.")
    if pending_question_ids(sections):
        return Plan({}, waiting=True)
    missing = [section for section in REQUIRED if not sections.get(section, "").strip()]
    if missing:
        raise RequirementsError(f"Missing test plan sections: {', '.join(missing)}")

    cases: dict[str, PlanCase] = {}
    rows: set[tuple[str, str, str]] = set()
    for line in sections[TRACE].splitlines():
        if not line.strip():
            continue
        row = re.fullmatch(
            rf"- ({FR_ID}) \| ({AC_ID}) \| ({CASE_ID}) \| (red|pass|manual|environment) \| ([^|]+) \| ([^|]+)",
            line.rstrip(),
        )
        if not row:
            raise RequirementsError("Use - FR-001 | AC-001 | TC-001 | mode | target | expectation.")
        fr, ac, case_id, mode, target, expectation = row.groups()
        case = PlanCase(mode, target.strip(), expectation.strip())
        if not case.target or not case.expectation or (fr, ac, case_id) in rows:
            raise RequirementsError(f"Empty or duplicate trace row: {case_id}")
        if case_id in cases and cases[case_id] != case:
            raise RequirementsError(f"Conflicting definitions of {case_id}.")
        rows.add((fr, ac, case_id))
        cases[case_id] = case
    if {(fr, ac) for fr, ac, _ in rows} != {(fr, ac) for fr, criteria in trace.items() for ac in criteria}:
        raise RequirementsError("Test trace must cover exactly the approved FR/AC pairs.")
    automated = [case.target for case in cases.values() if case.mode in ("red", "pass")]
    if len(automated) != len(set(automated)):
        raise RequirementsError("Reuse a TC ID instead of assigning multiple IDs to the same pytest node.")
    inputs = []
    for line in sections.get(INPUTS, "").splitlines():
        if not line.strip():
            continue
        entry = re.fullmatch(r"- ([^\r\n]+)", line.rstrip())
        if not entry or not relative_input(entry[1]) or entry[1] in inputs:
            raise RequirementsError("Use unique - relative/path entries for test input files.")
        inputs.append(entry[1])
    return Plan(cases, tuple(sorted(rows)), inputs=tuple(inputs))


def relative_input(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        bool(path.parts) and not path.is_absolute() and path.as_posix() == name
        and not set(path.parts).intersection({"..", ".git", ".venv", "runtime"})
        and not any(char in name for char in "\\:\x00")
    )


def pytest_file(node: str) -> str:
    filename, separator, selector = node.partition("::")
    path = PurePosixPath(filename)
    if (
        not separator or not re.fullmatch(r"(?:[A-Za-z_][\w]*::)*test_[\w]+(?:\[[^\]\r\n]+\])?", selector)
        or not relative_input(filename) or len(path.parts) <= len(OFFLINE_TEST_ROOT)
        or path.parts[:len(OFFLINE_TEST_ROOT)] != OFFLINE_TEST_ROOT
        or path.suffix != ".py"
    ):
        raise RequirementsError(f"Use an explicit pytest node under unit_tests/test_design/: {node}")
    return filename


def test_inputs(worktree: Path, base_commit: str, plan: Plan) -> dict[str, bytes]:
    files = set(plan.inputs)
    files.update(pytest_file(case.target) for case in plan.cases.values() if case.mode in ("red", "pass"))
    changed = read_git(
        worktree, "diff", "--no-ext-diff", "--name-only", "--no-renames", "-z", base_commit, "--", *TEST_ROOTS,
    )
    added = read_git(worktree, "ls-files", "--others", "--exclude-standard", "-z", "--", *TEST_ROOTS, "data")
    tracked = read_git(worktree, "ls-files", "-z", "--", *TEST_ROOTS, "data")
    fixtures = {
        raw for raw in tracked.split(b"\0")
        if raw and (raw.startswith(b"data/") or PurePosixPath(os.fsdecode(raw)).name == "conftest.py")
    }
    # Missing paths are absent from the inventory. Explicit plan inputs above
    # must still exist, even if they were deleted from the worktree.
    for raw in (set((changed + added).split(b"\0")) | fixtures) - {b""}:
        name = os.fsdecode(raw)
        try:
            (worktree / name).lstat()
        except FileNotFoundError:
            continue
        files.add(name)
    files.update(
        name for name in ("pytest.ini", ".pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg", "conftest.py")
        if os.path.lexists(worktree / name)
    )
    return {name: checked_artifact(worktree, worktree / name).read_bytes() for name in sorted(files)}


def file_hashes(files: dict[str, bytes]) -> dict[str, str]:
    return {name: sha256(data).hexdigest() for name, data in files.items()}


def execution_inputs(worktree: Path, inputs: dict[str, bytes]) -> dict[str, bytes]:
    """Capture tracked project files and the selected test inputs for an isolated pytest run."""
    files = inputs.copy()
    tracked = read_git(worktree, "ls-files", "-z")
    for raw in tracked.split(b"\0"):
        if not raw:
            continue
        name = os.fsdecode(raw)
        if name in files:
            continue
        if not relative_input(name):
            raise RequirementsError(f"Invalid tracked test execution path: {name}")
        path = worktree / name
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        files[name] = checked_artifact(worktree, path).read_bytes()
    return files


def bundle_digest(plan_data: bytes, files: dict[str, str], evidence: dict | None) -> str:
    manifest = {"plan_sha256": sha256(plan_data).hexdigest(), "files": files, "evidence": evidence}
    return sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def bundle_archive(plan_data: bytes, files: dict[str, bytes], evidence: dict | None) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("test-plan.md", plan_data)
        archive.writestr("evidence.json", json.dumps(evidence, sort_keys=True).encode("utf-8"))
        for name, data in files.items():
            archive.writestr(f"files/{name}", data)
    return output.getvalue()
