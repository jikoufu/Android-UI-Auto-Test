# 测试业务流程

现有 `SettingsFlow` 和 `NetworkFlow` 分别在 `settings_workflow.py`、`network_workflow.py`，仅组合 automation_core 中已有设备能力；不导入 backend、pytest、运行时 AI 或 agent_harness。

OTA 与翻译业务尚待需求、设备协议和基线定义，不创建假动作占位函数。`agent_harness/workflows/` 是研发和交付协议，与本目录不同。
