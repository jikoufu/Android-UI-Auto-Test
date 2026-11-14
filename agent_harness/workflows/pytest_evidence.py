"""Translate pytest reports into evidence for the Test Design approval gate."""

from pathlib import Path
from typing import Mapping

if __package__:
    from .pytest_runner import run_nodes
    from .requirements_document import RequirementsError
    from .test_design_document import PlanCase, pytest_file
else:
    from pytest_runner import run_nodes
    from requirements_document import RequirementsError
    from test_design_document import PlanCase, pytest_file

EVIDENCE_POLICY = "offline-unit-assertion-v3"


def classify(report: dict, node: str, case: PlanCase) -> dict:
    entries = report["reports"].get(node, [])
    phases = {entry["phase"]: entry for entry in entries}
    call = phases.get("call", {})
    reason = report["collection_error"]
    if report["process_attempted"]:
        reason = "Unsupported external process attempt. Use a fake or an environment test."
    elif not reason and report["exit_code"] not in (0, 1):
        reason = f"pytest exited with code {report['exit_code']}."
    elif not reason and (len(entries) != 3 or set(phases) != {"setup", "call", "teardown"}):
        reason = "Node did not complete setup, call and teardown exactly once."
    elif not reason:
        invalid = next((entry for entry in entries if entry["xfail"] or entry["outcome"] == "skipped"
                        or (entry["phase"] != "call" and entry["outcome"] != "passed")), None)
        if invalid:
            reason = f"{invalid['phase']}: {invalid['exception'] or invalid['outcome']}."
        elif call["outcome"] == "passed":
            if case.mode == "red":
                return {"status": "unexpected_green", "reason": "Expected a failing behavior assertion.", "phase": "call"}
            return {"status": "passed", "reason": "Test call passed.", "phase": "call"}
        elif case.mode == "red" and call["outcome"] == "failed" and call.get("test_assertion"):
            return {"status": "red", "reason": "Assertion failed in the test function.", "phase": "call",
                    "exception": call["exception"], "location": call["location"]}
        else:
            reason = f"call: {call['exception'] or call['outcome']}."
    return {"status": "error", "reason": reason}


def run_cases(worktree: Path, cases: Mapping[str, PlanCase]) -> dict:
    automated = {key: case for key, case in cases.items() if case.mode in ("red", "pass")}
    nodes = [case.target for case in automated.values()]
    if not nodes or len(nodes) != len(set(nodes)):
        raise RequirementsError("Specify unique pytest nodes. Reuse a TC ID for shared trace rows.")
    root = worktree.resolve()
    for node in nodes:
        file = root / pytest_file(node)
        if not file.is_file() or file.resolve() != file:
            raise RequirementsError(f"Expected an ordinary offline test module: {node}")
    report = run_nodes(root, nodes)
    outcomes = {
        key: {"node": case.target, "expected_behavior": case.expectation, **classify(report, case.target, case)}
        for key, case in automated.items()
    }
    return {key: report[key] for key in (
        "command", "pytest_args", "runner", "cwd", "environment", "exit_code", "runner_exit_code",
    )} | {"policy": EVIDENCE_POLICY, "outcomes": outcomes}
