# 项目政策入口

- 架构、依赖、设备与运行时 AI 约束以根 [AGENTS.md](../../AGENTS.md) 为准。
- 项目现状和运行方式见根 [README.md](../../README.md)；设计背景按任务需要从 `docs/` 读取。
- 修改测试时读取唯一规范 [.agents/skills/pytest-test-writer/SKILL.md](../../.agents/skills/pytest-test-writer/SKILL.md)。Harness 不维护其副本。
- 配置、依赖和凭证管理使用项目已有入口；Harness 不保存模型 API 地址、凭证或设备连接配置。

通用技能依赖本入口提供项目约束。迁移 Harness 时替换本入口的项目链接，不把产品业务逻辑写进通用技能。所有外部操作仍遵循当前会话的用户授权和工具权限，工作流本身不授予权限。
