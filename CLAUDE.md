# Claude 项目入口

先阅读并遵守根目录 [AGENTS.md](AGENTS.md)，以其作为项目规则的唯一来源；项目说明与实际运行方式见 [README.md](README.md)。

需要多步研发、测试或交付流程时，读取 [Agent Harness](agent_harness/README.md) 和 [意图路由](agent_harness/router/intent_router.md)，按用户需求选择工作流并手动执行。

Harness 通过文件协议协作，不是运行时 AI 恢复模块，也不会自动启动模型或执行命令。只报告实际完成的修改与检查，保留未验证项。
