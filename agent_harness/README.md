# Agent Harness

本目录提供可人工执行的研发文件协议，包括意图路由、四条 YAML 工作流、技能、工具适配说明、项目政策和离线校验。运行时 AI 实现在 `automation_core/ai/`。

在 Git 源码检出中，可手动调用 Codex 的 `$dev-workflow` 或 Claude Code 的 `/dev-workflow` 创建独立任务工作区和管理状态。执行范围和后续阶段见 [Dev Workflow](workflows/workflow.md)。

## 使用

1. 读取根目录 `AGENTS.md`，再按 [意图路由](router/intent_router.md) 选择工作流。项目约束入口是 [项目政策](policies/project.md)。
2. 将 `tasks/_template/` 复制为新的任务目录，例如 `tasks/ENG-002/`；修改 `task_id`、`title`、`workflow`，设 `template: false`，写明验收条件。任务编号由维护者分配，模板不代表已完成任务。
3. 在当前 Agent 会话中明确要求执行该 YAML 工作流。逐步读取其 `skill` 文件，按实际工作更新需求、设计和验证记录。YAML 是人工执行规范，没有自动调度器。
4. 将实际执行的命令、退出码和结果写入 `verification.json`。未运行时保持 `not_run`，受环境阻碍时记录 `blocked`；不得用命令计划冒充执行结果。
5. 运行离线校验，然后交付变更摘要、实际测试结果和未验证风险。

```powershell
python -m agent_harness.evaluations.validate
python -m agent_harness.evaluations.validate --task tasks/ENG-002
python -m pytest unit_tests/agent_harness -q
```

第一条检查四条工作流、技能引用及任务模板；第二条额外检查指定任务。校验器只读取本地文件，不执行任务中的命令，也不能证明记录中的命令确实运行过。执行真实性仍须依靠日志和审阅。

## 边界与单一来源

- `workflows/` 是测试业务编排；`agent_harness/workflows/` 是研发流程。
- 通用技能不包含设备驱动、产品导航路径、服务凭证或 Provider 配置。移植到其他项目时调整项目政策与入口链接。
- `.agents/skills/pytest-test-writer/SKILL.md` 是本项目测试编写约定的唯一来源，由项目政策按适用范围引用。
- `schemas/protocol.py` 是任务、工作流和验证记录的数据规范；使用已有 Pydantic 依赖。可通过各模型的 `model_json_schema()` 获取 JSON Schema，不另维护一份副本。
- `adapters/` 目前是 Codex、Claude、DeepSeek 的手动接入说明，不表示已安装 CLI、配置 API 或实现服务集成。
- 运行日志、截图等放入根项目规定的 `runtime/`；`tasks/` 仅保存脱敏后的研发记录与证据引用。

角色分离依据实际执行者记录。由同一 Agent 自查时写 `self`，不能宣称完成独立验证。独立验证需要另一执行者实际审阅并重新执行相应检查。
