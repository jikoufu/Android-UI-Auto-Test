# 意图路由

先读取根 `AGENTS.md` 和 `agent_harness/policies/project.md`。用户明确指定工作流时优先遵循；混合请求选择主要交付目标，必要时连续执行相关步骤，不重复生成同一份需求或配置。

| 主要交付目标 | 工作流 | 结束条件 |
| --- | --- | --- |
| 增加能力、改架构、修复实现 | `agent_harness/workflows/feature_development.yaml` | 实现与验收条件一致，有实际验证与边界说明 |
| 新增或修改测试场景、断言 | `agent_harness/workflows/test_development.yaml` | 测试可收集，相关检查完成，依赖要求明确 |
| 验证并交付已有变更或测试资产 | `agent_harness/workflows/test_delivery.yaml` | 检查结果、证据和未验证项齐全 |
| 审核失败证据、确定缺陷是否成立 | `agent_harness/workflows/defect_review.yaml` | 区分已确认、无法复现与证据不足，说明依据 |

纯问答、简单文案修改不必建立完整研发任务。需要记录多步研发过程时，从 `tasks/_template/` 新建任务，按模型填写记录。缺少会改变实现方向的需求时再向用户澄清；可以独立开展的调查不必等待。
