"""Check a design draft against its approved requirements revision."""

from dataclasses import dataclass
import re

if __package__:
    from .requirements_document import FR_ID, QUESTIONS, RequirementsError, markdown_sections, pending_question_ids
else:
    from requirements_document import FR_ID, QUESTIONS, RequirementsError, markdown_sections, pending_question_ids


METADATA = "文档元数据"
TRACE = "FR 与技术设计的追踪矩阵"
REQUIRED_SECTIONS = (
    METADATA, "设计目标与非目标", "当前系统架构及相关实现", "受影响模块与变更范围",
    "方案概述", "关键业务流程", "异常处理与边界条件", "兼容性与风险", TRACE,
    "技术决策", QUESTIONS, "下一阶段测试设计的输入约定",
)


@dataclass(frozen=True)
class DesignReview:
    ready: bool
    requires_requirements_revision: bool = False


def review_design(data: bytes, *, task_id: str, requirements_sha256: str,
                  requirements_approval_id: str, functions: set[str]) -> DesignReview:
    try:
        sections = markdown_sections(data.decode("utf-8-sig"), record_sections=(METADATA, TRACE, QUESTIONS))
    except UnicodeError as exc:
        raise RequirementsError("Design must be saved as UTF-8 Markdown.") from exc

    expected = {
        "task_id": task_id,
        "requirements_sha256": requirements_sha256,
        "requirements_approval_id": requirements_approval_id,
    }
    metadata: dict[str, str] = {}
    for line in sections.get(METADATA, "").splitlines():
        if not line.strip():
            continue
        entry = re.fullmatch(r"- ([a-z0-9_]+): (\S+)", line.rstrip())
        if not entry or entry[1] in metadata:
            raise RequirementsError("Use one - key: value line per design metadata field.")
        metadata[entry[1]] = entry[2]
    version = metadata.pop("design_version", "")
    if metadata != expected or not re.fullmatch(r"[1-9][0-9]*", version):
        raise RequirementsError("Design metadata must match the task and current Requirements approval, with a version.")

    question_ids = pending_question_ids(sections, allow_revision=True)
    if question_ids:
        return DesignReview(ready=False, requires_requirements_revision=any(
            question.startswith("RQ-") for question in question_ids
        ))

    missing = [heading for heading in REQUIRED_SECTIONS if not sections.get(heading, "").strip()]
    if missing:
        raise RequirementsError(f"Missing or empty design sections: {', '.join(missing)}")
    traced: set[str] = set()
    for line in sections[TRACE].splitlines():
        if not line.strip():
            continue
        row = re.fullmatch(rf"- ({FR_ID}): ([^|]+)\|([^|]+)\|([^|]+)", line.rstrip())
        if not row or row[1] in traced or not all(value.strip() for value in row.groups()[1:]):
            raise RequirementsError("Use one design trace per FR: - FR-001: module | interface | design.")
        traced.add(row[1])
    if traced != functions:
        raise RequirementsError("Design trace must cover all and only the approved FR identifiers.")
    return DesignReview(ready=True)
