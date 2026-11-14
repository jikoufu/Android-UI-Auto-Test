"""Run reviewed Test Design nodes with pytest and capture their execution reports."""

from __future__ import annotations

import ast
import dis
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
from tempfile import TemporaryDirectory

if __package__:
    from .requirements_document import RequirementsError

TIMEOUT = 120
MARKERS = "not device and not ai"
HOST_ENV = {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "PATH", "PATHEXT", "TMP", "TEMP", "TMPDIR", "LANG", "LC_ALL"}


def assertion_location(path: Path, position: dis.Positions) -> tuple[int | None, bool]:
    """Return the assertion's starting line and whether its location is ambiguous."""
    line, end_line, column, end_column = position
    if None in position:
        return None, False
    try:
        tree = ast.parse(path.read_bytes())
    except (OSError, SyntaxError, ValueError):
        return None, False
    statements = [node for node in ast.walk(tree) if isinstance(node, ast.stmt)]
    matches = [node for node in statements if isinstance(node, ast.Assert) and node.lineno <= line <= node.end_lineno]
    if len(matches) != 1:
        return None, len(matches) > 1
    assertion = matches[0]
    if (
        (line, column) < (assertion.lineno, assertion.col_offset)
        or (end_line, end_column) > (assertion.end_lineno, assertion.end_col_offset)
    ):
        return None, False
    for node in statements:
        if node is assertion:
            continue
        if assertion.lineno <= node.lineno <= assertion.end_lineno or (
            node.end_lineno == assertion.lineno and node.end_col_offset <= assertion.col_offset
        ):
            # A compound statement's header and its own inline assertion
            # describe one failure site, unlike independent sibling statements.
            if assertion not in ast.walk(node):
                return None, True
    return assertion.lineno, False


def clean_python() -> Path:
    configured = os.environ.get("TV_TEST_DESIGN_PYTHON", "")
    python = Path(configured)
    if not python.is_absolute() or not python.is_file():
        raise RequirementsError("Set TV_TEST_DESIGN_PYTHON to the absolute Python path of a clean Test Design venv.")
    root = python.parent.parent
    settings = root / "pyvenv.cfg"
    packages = [root / "Lib" / "site-packages"] if os.name == "nt" else list((root / "lib").glob("python*/site-packages"))
    if (not settings.is_file()
            or not re.search(r"^include-system-site-packages\s*=\s*false\s*$", settings.read_text(encoding="utf-8"), re.M | re.I)
            or not any(path.is_dir() for path in packages)):
        raise RequirementsError("Test Design needs a separate venv without system site-packages.")
    for directory in packages:
        if any(directory.glob("*.pth")) or any(directory.glob("*customize.py")) or any(directory.glob("*.egg-link")):
            raise RequirementsError("Test Design venv must use wheel installs without Python startup hooks or editable packages.")
    return python


def run_nodes(worktree: Path, nodes: list[str]) -> dict:
    python = clean_python()
    with TemporaryDirectory(prefix="td-pytest-") as temporary:
        directory = Path(temporary)
        output = directory / "result.json"
        args = [
            "-c", os.devnull, "--rootdir", str(worktree),
            "--confcutdir", str(worktree / "unit_tests" / "test_design"),
            "--import-mode=importlib", "-p", "no:cacheprovider", "-m", MARKERS, "-q", *nodes,
        ]
        command = [str(python), "-I", "-B", str(Path(__file__).resolve()), str(output), *args]
        env = {name: value for name, value in os.environ.items() if name.upper() in HOST_ENV}
        env.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", TV_AUTOMATION_HOME=str(worktree),
                   TV_AUTOMATION_RUNTIME_DIR=str(worktree / "runtime"))
        try:
            process = subprocess.run(command, cwd=worktree, env=env, capture_output=True, timeout=TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            raise RequirementsError(f"pytest timed out after {TIMEOUT} seconds; no evidence recorded.") from exc
        try:
            result = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RequirementsError(
                f"pytest runner produced no report (exit {process.returncode}). Check the dedicated venv dependencies."
            ) from exc
    return {
        **result, "command": command, "pytest_args": args, "runner": "pytest with fixed offline configuration",
        "cwd": str(worktree), "exit_code": process.returncode, "runner_exit_code": process.returncode,
    }


def worker(output: Path, args: list[str]) -> int:
    # Supplemental guard for the reviewed, in-process unit test profile.
    process_attempted = False

    def reject_process(event, _args):
        nonlocal process_attempted
        if event in {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn",
                     "os.fork", "os.forkpty", "_winapi.CreateProcess", "os.startfile", "os.startfile/2"}:
            process_attempted = True
            raise RuntimeError("External processes are unsupported by the offline test profile.")

    sys.addaudithook(reject_process)
    import pytest

    reports = {}
    collection_error = None

    class Collector:
        def pytest_collectreport(self, report):
            nonlocal collection_error
            if report.failed:
                detail = str(report.longrepr)
                kind = next((name for name in ("SyntaxError", "ModuleNotFoundError", "ImportError") if name in detail), None)
                collection_error = f"Collection failed ({kind or 'collection error'})."

        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_makereport(self, item, call):
            result = yield
            report = result.get_result()
            error = call.excinfo
            entry = {
                "phase": call.when, "outcome": report.outcome, "xfail": hasattr(report, "wasxfail"),
                "exception": error.typename if error else None,
            }
            if error and error.type is AssertionError:
                origin = error.value.__traceback__
                while origin is not None and origin.tb_next is not None:
                    origin = origin.tb_next
                entry["test_assertion"] = False
                if origin is not None and origin.tb_frame.f_code is getattr(item.obj, "__code__", None):
                    # Native operations can raise AssertionError in the caller's frame.
                    # Only the assertion's own raise instruction is a Red candidate.
                    instruction = next(
                        (op for op in dis.get_instructions(origin.tb_frame.f_code)
                         if op.offset == origin.tb_lasti),
                        None,
                    )
                    if instruction is not None and instruction.opname == "RAISE_VARARGS" and instruction.positions is not None:
                        line, ambiguous = assertion_location(item.path, instruction.positions)
                        entry["test_assertion"] = line is not None
                        entry["ambiguous_assertion"] = ambiguous
                        if line is not None:
                            entry["location"] = {"file": item.location[0], "line": line}
            reports.setdefault(item.nodeid, []).append(entry)

    sys.path.insert(0, str(Path.cwd()))
    code = pytest.main(args, plugins=[Collector()])
    output.write_text(json.dumps({
        "reports": reports, "collection_error": collection_error, "process_attempted": process_attempted,
        "environment": {"python": platform.python_version(), "pytest": pytest.__version__, "os": platform.system()},
    }), encoding="utf-8")
    return int(code)


if __name__ == "__main__":
    raise SystemExit(worker(Path(sys.argv[1]), sys.argv[2:]))
