# 模块化单体与迁移说明

同一仓库保存源码和研发协议；当前只运行一个 FastAPI 本地服务。设备核心可由 pytest、脚本或后端调用，不引入微服务、消息队列或 Appium。

## 迁移映射

| 原路径 | 新路径 |
| --- | --- |
| `devices/adb.py` | `automation_core/device/adb_client.py` |
| `devices/ui.py` | `automation_core/device/uia2_client.py` |
| `devices/tv.py` | `automation_core/device/device_manager.py`（保留 AndroidTV 类型） |
| `devices/remote.py` | `automation_core/remote/controller.py`（ADB keyevent） |
| `ai/context.py` | `automation_core/evidence/collector.py` |
| `ai/trace.py` | `automation_core/evidence/trace.py` |
| `ai/executor.py` 中的页面指纹 | `automation_core/navigation/page_detector.py` |
| `ai/` 其余实现 | `automation_core/ai/` |
| `models/` | `automation_core/models/` |
| `flows/settings_flow.py`、`network_flow.py` | `workflows/settings_workflow.py`、`network_workflow.py` |
| `utils/config.py` | `automation_core/config.py` |
| `config/device.yaml`、`ai.yaml` | `configs/devices.yaml`、`models.yaml` |
| 根 `conftest.py` | `tests/conftest.py`；装配迁入 `automation_core/bootstrap.py` |
| `tests/test_*.py` | `tests/system/test_*.py` |
| `unit_tests/test_*.py` | `unit_tests/automation_core/test_*.py` |
| `pytest.ini`、旧 requirements 依赖表 | `pyproject.toml` |
| `reports/` 新输出 | `runtime/evidence/`、`runtime/logs/`、`runtime/reports/` |

新代码统一使用新导入路径，不保留两套源代码或旧顶层导入兼容包。原本空白的工具模块不保留抽象空壳。旧报告留在本机原位置并忽略。`ADBClient.reports_dir` 参数名保留兼容现有调用，其默认位置现在是 `runtime/evidence`；执行器日志默认单独进入 `runtime/logs`。

## 保留的行为

迁移以工作区当前内容为源，包括迁移前尚未提交的 UI transport 自愈、导航候选、组合定位、未知动作结果校验及对应单元测试。无界重试、重复导航入口、未知写操作结果重放仍受原约束限制。

设备初始化从 pytest 生命周期中提取为普通 Python 工厂，构造不访问设备；AI 客户端仅在显式创建时检查凭证。页面指纹算法移入 navigation，无业务行为改变。设备/AI 真实执行仍需对应环境。

## 当前切分

- `frontend` 只经 HTTP 读后端，不操作设备。
- `backend` 分 API / service / schema / dependency；只读用例目录由 AST 获取，不导入或执行测试。
- `automation_core` 不导入 workflows、Web、pytest 或 Harness，设备层不导入 AI。
- `workflows` 只组合已有设备能力。运行时 AI 工具在核心内，仅调用核心能力，避免向上依赖业务流程。
- `agent_harness` 的通用技能与协议只读项目文件；项目特定规则由 policy 引用根 AGENTS。手动适配说明不代表 CLI/API 集成。

`unit_tests/automation_core/test_architecture.py` 固化依赖与 UIA2 唯一适配层约束。代码的跨层连接和按需装配由离线测试保护。

## 后续阶段

先定义 OTA/翻译目标设备、输入数据、验收和恢复边界，再实现对应 workflows 与 tests；之后增加本地任务队列、设备占用与取消、结果持久化和权限。VLM、USB IR 与 Agent Runner 分别是独立待实现能力，不以目录存在作为完成依据。源码 ZIP 是当前部署形式，普通 Python wheel 不是完整平台发行包。
