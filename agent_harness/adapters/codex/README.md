# Codex 手动入口

在项目根目录打开 Codex，先读取 `AGENTS.md`，再明确引用意图路由或工作流，例如：

> 按 agent_harness/workflows/feature_development.yaml 完成当前需求，使用 tasks/ENG-002 记录范围与真实验证结果。

按工作流逐项读取技能文件即可使用；目录中的技能不会因为存在于 Harness 下而被所有客户端自动发现。无需复制已有 `.agents/skills` 规范。当前没有 CLI 调度、Provider 集成或后台 Runner。
