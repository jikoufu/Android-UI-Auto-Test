"""Check the acceptance report against the approved test plan and recorded pytest run."""

from datetime import date
import re

if __package__:
    from .requirements_document import AC_ID, FR_ID, RequirementsError, markdown_sections
    from .test_design_document import CASE_ID, Plan
else:
    from requirements_document import AC_ID, FR_ID, RequirementsError, markdown_sections
    from test_design_document import CASE_ID, Plan


PLACEHOLDERS = {"-", "none", "n/a", "na", "tbd", "todo", "unknown", "placeholder", "unavailable", "self", "无", "待定"}


def has_details(value: str) -> bool:
    return bool(value.strip()) and value.strip().rstrip(".。").casefold() not in PLACEHOLDERS


def review_acceptance(data: bytes, metadata: dict[str, str], plan: Plan, evidence: dict | None) -> str:
    try:
        sections = markdown_sections(data.decode("utf-8-sig"), record_sections=("Version", "Trace", "Tests", "Conclusion"))
    except UnicodeError as exc:
        raise RequirementsError("Acceptance report must be UTF-8 Markdown.") from exc
    for name in ("Version", "Upstream", "Trace", "Tests", "Not Run", "Code Review", "Risks", "Conclusion"):
        if not sections.get(name, "").strip():
            raise RequirementsError(f"Acceptance report requires a nonempty {name} section.")
    version = sections["Version"].splitlines()
    for key, value in metadata.items():
        row = f"- {key}: {value}"
        if row not in version:
            raise RequirementsError(f"Acceptance report does not identify the current {key}.")
        version.remove(row)
    reviewer = re.fullmatch(r"- independent_reviewer: (\S.*)", version[0]) if len(version) == 1 else None
    if not reviewer:
        raise RequirementsError("Identify the independent reviewer, or use unavailable.")
    conclusion = sections["Conclusion"].strip()
    if conclusion not in ("PASS", "PASS WITH RISKS", "FAIL", "BLOCKED"):
        raise RequirementsError("Acceptance conclusion must be PASS, PASS WITH RISKS, FAIL or BLOCKED.")
    passing = conclusion in ("PASS", "PASS WITH RISKS")
    if passing and not has_details(reviewer[1]):
        raise RequirementsError("A passing Acceptance report requires an identified independent reviewer.")
    if passing and not has_details(sections["Code Review"]):
        raise RequirementsError("A passing Acceptance report requires actual code review findings.")
    rows = {}
    for line in sections["Trace"].splitlines():
        if not line.strip():
            continue
        row = re.fullmatch(rf"- ({FR_ID}) / ({AC_ID}) / ({CASE_ID}): (PASS|FAIL|NOT RUN) \| (\S.*)", line.rstrip())
        if not row or row.group(1, 2, 3) in rows:
            raise RequirementsError("Use unique '- FR-001 / AC-001 / TC-001: PASS|FAIL|NOT RUN | evidence' rows.")
        rows[row.group(1, 2, 3)] = row.group(4, 5)
    if rows.keys() != set(plan.trace):
        raise RequirementsError("Acceptance trace must cover all and only the approved FR/AC/TC rows.")
    for (_, _, case_id), (outcome, details) in rows.items():
        if plan.cases[case_id].mode in ("red", "pass"):
            actual = (evidence or {}).get("outcomes", {}).get(case_id, {}).get("status")
            expected = "NOT RUN" if actual in (None, "not_verified") else ("PASS" if actual == "passed" else "FAIL")
            if outcome != expected:
                raise RequirementsError(f"{case_id} must match independent pytest evidence: {expected}.")
        elif outcome == "PASS":
            observed = re.fullmatch(r"([0-9]{4}-[0-9]{2}-[0-9]{2}) \| ([^|]+) \| ([^|]+)", details)
            if not observed or not all(has_details(field) for field in observed.group(2, 3)):
                raise RequirementsError(
                    f"{case_id} requires actual manual/environment evidence: YYYY-MM-DD | run reference | observation. "
                    "If it was not verified, use NOT RUN and BLOCKED."
                )
            try:
                date.fromisoformat(observed[1])
            except ValueError as exc:
                raise RequirementsError(f"{case_id} has an invalid evidence date.") from exc
    # The orchestration run is stored separately. These are the reviewer's additional commands.
    tests = sections["Tests"].strip()
    for line in tests.splitlines() if tests != "None" else ():
        if not line.strip():
            continue
        run = re.fullmatch(r"- (PASS|FAIL) \| (-?[0-9]+) \| \S.*", line.rstrip())
        if not run or (run[1] == "PASS") != (int(run[2]) == 0):
            raise RequirementsError("Use '- PASS|FAIL | exit code | command and result' for additional test runs.")
        if passing and run[1] == "FAIL":
            raise RequirementsError("A failed test run cannot support a passing Acceptance report.")
    if passing:
        if any(case.mode in ("red", "pass") for case in plan.cases.values()) and (
            not evidence or evidence.get("exit_code") != 0
        ):
            raise RequirementsError("Run and pass the approved automated cases in Acceptance.")
        if any(outcome != "PASS" for outcome, _ in rows.values()):
            raise RequirementsError("Passing Acceptance requires evidence for every approved trace row.")
        if conclusion == "PASS" and sections["Not Run"].strip() != "None":
            raise RequirementsError("Use PASS WITH RISKS for explicitly permitted nonblocking omissions.")
        if conclusion == "PASS WITH RISKS" and sections["Risks"].strip() == "None":
            raise RequirementsError("Record the nonblocking risks for PASS WITH RISKS.")
    return conclusion
