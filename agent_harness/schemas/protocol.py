"""File contracts for manual development workflows; no execution or device code."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProtocolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class WorkflowStep(ProtocolModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    skill: str = Field(min_length=1)
    needs: list[str]
    outputs: list[str]


class Workflow(ProtocolModel):
    protocol_version: Literal[1]
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    description: str = Field(min_length=1)
    policies: list[str] = Field(min_length=1)
    steps: list[WorkflowStep] = Field(min_length=1)

    @model_validator(mode="after")
    def ordered_steps(self) -> Self:
        seen: set[str] = set()
        for step in self.steps:
            if step.id in seen:
                raise ValueError("step ids must be unique")
            if not set(step.needs).issubset(seen):
                raise ValueError("step dependencies must reference preceding steps")
            seen.add(step.id)
        return self


class TaskArtifacts(ProtocolModel):
    requirement: str = Field(min_length=1)
    design: str = Field(min_length=1)
    verification: str = Field(min_length=1)


class TaskRecord(ProtocolModel):
    protocol_version: Literal[1]
    task_id: str = Field(pattern=r"^[A-Z][A-Z0-9]*-[A-Z0-9]+$")
    template: bool = False
    title: str = Field(min_length=1)
    workflow: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    status: Literal["draft", "active", "blocked", "complete"]
    acceptance_criteria: list[str]
    artifacts: TaskArtifacts

    @model_validator(mode="after")
    def task_scope(self) -> Self:
        if self.template and self.status != "draft":
            raise ValueError("templates must stay draft")
        if not self.template and not self.acceptance_criteria:
            raise ValueError("tasks require acceptance criteria")
        if any(not item.strip() for item in self.acceptance_criteria):
            raise ValueError("acceptance criteria must not be blank")
        return self


class CheckRecord(ProtocolModel):
    command: str = Field(min_length=1)
    result: Literal["passed", "failed", "not_run", "blocked", "skipped"]
    exit_code: int | None
    summary: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def execution_result(self) -> Self:
        if self.result == "passed" and self.exit_code != 0:
            raise ValueError("passed checks require exit_code 0")
        if self.result == "failed" and self.exit_code == 0:
            raise ValueError("failed checks cannot have exit_code 0")
        if self.result in {"not_run", "blocked", "skipped"} and self.exit_code is not None:
            raise ValueError("unexecuted checks cannot have an exit_code")
        return self


class VerificationRecord(ProtocolModel):
    protocol_version: Literal[1]
    status: Literal["not_run", "passed", "failed", "blocked", "partial"]
    review_mode: Literal["unassigned", "self", "independent"]
    reviewer: str | None
    checks: list[CheckRecord]
    limitations: list[str]

    @model_validator(mode="after")
    def verification_result(self) -> Self:
        results = {check.result for check in self.checks}
        executed = bool(results & {"passed", "failed"})
        if self.review_mode != "unassigned" and not (self.reviewer or "").strip():
            raise ValueError("assigned reviews require a reviewer identifier")
        if executed and self.review_mode == "unassigned":
            raise ValueError("executed checks require a review assignment")
        if self.status == "passed" and (not self.checks or results != {"passed"}):
            raise ValueError("passed verification requires only executed passing checks")
        if self.status == "failed" and "failed" not in results:
            raise ValueError("failed verification requires an executed failing check")
        if self.status in {"not_run", "blocked"} and executed:
            raise ValueError("unexecuted verification cannot contain executed checks")
        if self.status == "partial" and (not executed or "failed" in results):
            raise ValueError("partial verification requires executed checks without unresolved failures")
        if self.status != "passed" and not any(item.strip() for item in self.limitations):
            raise ValueError("incomplete or failed verification requires limitations")
        return self
