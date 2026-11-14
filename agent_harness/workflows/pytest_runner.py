"""Run reviewed Test Design nodes with pytest and capture their execution reports."""

from __future__ import annotations

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
                origin = error.traceback[-1]
                entry["test_assertion"] = (
                    origin.frame.code.raw is getattr(item.obj, "__code__", None)
                    and bool(re.match(r"^assert\b", str(origin.statement).lstrip()))
                )
                entry["location"] = {"file": item.location[0], "line": origin.lineno + 1}
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
