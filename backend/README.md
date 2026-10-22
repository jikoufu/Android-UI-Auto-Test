# 本地 API

FastAPI 负责 HTTP 适配，`services/` 负责查询编排，`dependencies/` 组装 `automation_core` 的现有能力，`schemas/` 定义公开数据。设备驱动、AI 推理和测试流程不在后端重复实现。

从项目根目录运行：

```powershell
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

`/` 提供静态控制台，`/docs` 提供 OpenAPI 交互文档。服务仅面向本机使用；权限、认证和远程部署尚未实现。导入和启动应用不连接设备或 AI，只有请求设备接口时才读取 ADB 列表。

| GET 接口 | 实际能力 |
| --- | --- |
| `/api/v1/health` | API 存活状态；不检查设备或 AI |
| `/api/v1/devices` | 调用核心 ADB 枚举；失败返回 503，空列表代表未发现设备 |
| `/api/v1/tasks/catalog` | 静态解析真实测试函数；不等同于 Pytest 参数化收集结果 |
| `/api/v1/reports` | 列出 `runtime/reports` 内的文件元数据，最多 200 项 |
| `/api/v1/reports/{path}` | 限定报告目录内的附件下载，拒绝路径穿越与目录外链接 |
| `/api/v1/settings` | 只返回配置文件存在与能力实施状态，不返回配置内容或凭证 |
| `/api/v1/defects` | 明确返回缺陷管理待实现状态 |

Web 任务调度、缺陷管理和 Agent Runner 尚未实现，不提供伪成功的执行接口。当前测试仍以 Pytest 命令行运行；业务流程与核心可独立于 FastAPI 使用。
