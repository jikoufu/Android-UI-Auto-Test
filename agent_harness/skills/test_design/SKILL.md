---
name: test-design
description: 将验收条件映射为必要的测试场景、断言和依赖隔离方式，适用于新增或调整测试前的设计。
---

# 测试设计

## dev-workflow

只从已授权的 `dev-workflow` 调用本阶段。使用 `test-design-input` 返回的批准需求、批准设计、任务 ID 和 Worktree。检查该 Worktree 的 `pyproject.toml`、CI 配置（如有）、测试目录、fixture、mock/fake、数据和标记。修改 `tests/` 时遵守项目测试编写 Skill。只修改测试与 `test-plan.md`。业务实现、设备和外部服务留待后续授权。

按 FR/AC 检查正常、异常、边界及相关权限、并发、数据完整性和兼容性。选择能有效验证行为的最低成本层级。自动化用例尽量复用现有 fixture，并写真实、独立、可执行的业务断言。尚未实现的能力可通过现有入口的可观察行为得到 Red，避免把缺失导入、fixture 或依赖当作失败断言。已有行为满足需求时应验证覆盖并记录通过。

在 `docs/changes/<task_id>/test-plan.md` 使用下列二级标题：

- 文档元数据
- 测试目标与范围
- 测试层级及选择理由
- 测试场景和用例
- 测试数据与环境依赖
- Mock / Fixture 设计
- FR / AC / 测试追踪矩阵
- 自动化与人工测试边界
- Red 测试执行结果
- 已知限制和风险
- 待确认问题

元数据使用 `- key: value`，填写 `task_id`、`requirements_sha256`、`requirements_approval_id`、`design_sha256`、`design_approval_id` 和正整数 `test_plan_version`。问题使用 `- Q-001 具体问题` 或只写 `无`。

每个批准的 FR/AC 配对至少一条追踪记录。格式为 `- FR-001 | AC-001 | TC-001 | red | unit_tests/test_design/test_example.py::test_expected_behavior | 目标行为应出现`。模式可选 `red`、`pass`、`manual`、`environment`。最后一列写可观察的预期行为。人工和环境用例另说明无法在本地自动执行的原因。可复用 TC 编号，目标和预期须一致；同一 pytest 节点只对应一个 TC 编号。

在 Red 测试执行结果一节说明预计 Red、回归检查以及未执行项。用编排器的 `test-run` 取得实际证据。结构化证据随任务状态保存，包含命令、节点、环境、退出码和逐项结果，无需把输出写回计划。直接在测试函数中断言目标行为。Red 记录测试调用阶段、断言位置和计划预期，提交审批时展示失败断言及其与 FR/AC 的关系，由用户确认。测试、fixture 或计划变化后重新运行。

仓库 `data/` 下的受跟踪及未忽略文件会纳入测试证据。测试依赖的其他文件（如目录外的共享 fixture、辅助模块或配置）在可选的 `## 测试输入文件` 中逐行声明，格式为 `- 相对于Worktree的文件路径`。仅使用可归档的脱敏测试输入。变更输入后重新运行测试。

本地自动取证只运行 `unit_tests/test_design/` 中经过检查的纯逻辑测试。检查测试模块、导入和 fixture，确保收集与执行都无需设备、网络或子进程。使用替身验证适用层级，其他场景标记为 environment。执行环境和批准归档规则见 [共享 Workflow](../../workflows/workflow.md#test-design-调用与确认)。

## 既有 Harness YAML 人工流程

阅读项目政策中指定的测试约定和现有测试，按验收条件选择正常、失败或边界场景。设计应验证可观察行为，不重复实现细节。

在 `design.md` 记录场景、前置条件、操作、预期结果和证据。明确哪些检查可使用 fake/mock 离线执行，哪些必须连接外部依赖；离线替身的结果不能代表真实集成成功。

尽量复用既有 fixture 和业务能力。只设计足以覆盖本次变更风险的检查，避免为低影响文字或布局调整增加机械测试。
