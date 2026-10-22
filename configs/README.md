# 配置唯一入口

devices.yaml：设备连接与有限通信重试。models.yaml：兼容服务、模型、凭证环境变量名和恢复约束。environments.yaml：本机后端。accounts.example.yaml：未来账户环境变量名称示例。

凭证只能放进进程环境或本机密钥存储；不在本目录维护真实账户文件。路径和运行产物根由 automation_core/config.py 统一解析，避免重复配置。现有 DeepSeek 模型字段来自迁移前配置，需在真实 Provider 环境验证。
