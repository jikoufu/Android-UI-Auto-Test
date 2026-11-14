---
name: independent-verification
description: 根据验收条件审阅变更并重新执行相关检查，记录执行者及验证范围，适用于研发或交付阶段的质量验证。
---

# 验证

从需求和实际变更推导检查项，不直接接受实现者的成功结论。阅读项目政策与证据规则，在当前任务 `verification.json` 中记录实际命令、退出码、结果和环境限制。

独立验证由另一执行者开展；记录 `review_mode: independent` 时必须填写实际验证者标识。若只有实现者本人可用，使用 `self`，明确为自查并如实交付。

审查代码时，以基线到待审版本的差异和已批准范围为依据，纳入待审的未提交变更。检查新增层、重复业务规则和自建机制是否有当前需求支撑，是否可复用已有代码或框架能力。发现可简化的问题时，指出文件位置、实际维护代价及保留现有行为的更小方案，按影响定级。

至少核对关键验收行为和受影响调用方。检查失败时保留失败记录和原因；修复后重新执行并追加检查结果。汇总状态依据最终相关检查，历史失败可留在日志引用，不得将尚未解决的失败覆盖成通过。

缺少环境时记录 `blocked` 或 `not_run`；只完成部分检查时使用 `partial` 并写出限制。记录中的 `passed` 仅覆盖已执行检查，不能扩展为真实集成或设备能力已验证。此技能不会自动发布、提交缺陷或发送消息。

## Dev Workflow 的 Acceptance 阶段

收到 Orchestrator 返回的已批准输入后，由独立于实现的审阅者在绑定 Worktree 核对基线到最终版本的差异，包括未提交变更。按批准的 FR/AC 逐条审查实现、回归和业务边界；核对设计约束、安全、兼容性、改动范围及复杂度。见 [共享 Workflow](../../workflows/workflow.md) 的复测及人工审批命令。阶段产物为 `acceptance.md`，本阶段无需额外生成 `verification.json`。

使用以下二级标题。结构化记录保持单行。

- `Version` 使用 `- key: value` 逐行写入 `task_id`、`requirements_sha256`、`design_sha256`、`test_design_sha256`、`development_sha256` 和 `independent_reviewer`（实际审阅者标识）。没有独立审阅者时写 `unavailable` 并报 BLOCKED。
- `Upstream` 列出批准的需求、设计、测试方案、开发记录及归档引用。
- `Trace` 每个批准的组合单独写一条，例如 `- FR-001 / AC-001 / TC-001: PASS | 证据位置和实际观察`。自动化用例引用本轮 `acceptance_evidence`。人工或环境用例的 PASS 使用 `PASS | YYYY-MM-DD | 执行记录位置或 ID | 实际观察`，由审阅者确认记录可查且对应批准版本。未执行写 NOT RUN 及原因，关键用例未执行时结论为 BLOCKED。
- `Tests` 记录额外实际执行的测试，格式为 `- PASS | 0 | 命令及实测结果` 或 `- FAIL | 1 | 命令及实测结果`，无额外命令则写 `None`。编排器执行的命令、退出码、节点结果和环境另存于 `acceptance_evidence`，在追踪行引用。
- `Not Run` 记录没有执行的场景（包括 pytest skip）、限制和影响，全部适用检查已执行时写 `None`。
- `Code Review` 记录核对范围、代码位置、发现及影响。`Risks` 写剩余风险，确无风险时写 `None`。
- `Conclusion` 单独写 PASS、PASS WITH RISKS、FAIL 或 BLOCKED。通过须逐条满足批准的追踪；只有报告明确解释、且人工认可的额外非阻塞风险可保留。关键条件失败写 FAIL。必要证据、环境或人工决策缺失写 BLOCKED。适用的设备或外部系统验证须有单独授权。
