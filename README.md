# Android TV Automation Platform

Android / Android TV 自动化平台，采用 **Monorepo（单仓库）+ 模块化单体 + 独立 Agent Harness**。前端、FastAPI 后端、自动化核心、pytest 场景和研发工作流同库管理，保持单向依赖。

当前完成第一阶段目录与边界重构：迁移既有 ADB、uiautomator2、遥控器、AI Step 恢复和系统设置场景，新增可运行的本地只读控制台与研发文件协议。平台不依赖前后端即可通过 Python / pytest 使用核心。

## 能力状态

| 模块 | 已实现 | 待开发 / 待验证 |
| --- | --- | --- |
| Automation Core | ADB、UIA2 适配、设备组合、ADB 遥控按键、页面指纹、证据采集 | USB IR 硬件适配；本次未运行真实设备 |
| 运行时 AI | OpenAI 兼容客户端、结构化分析、有界 Step 恢复、审计日志 | Provider 凭证与联机验证；VLM / 图像定位未实现 |
| 业务流程与场景 | Settings / Network 流程，系统设置及 AI 导航场景 | OTA、翻译流程和场景尚未实现 |
| Backend | 健康状态、设备查询、真实用例目录、报告索引及附件下载 | 任务下发、队列、权限、缺陷持久化 |
| Frontend | 同源静态控制台、用例/设备/报告状态展示 | 执行任务与缺陷审核交互 |
| Agent Harness | 意图路由、4 条研发工作流、7 个技能、文件协议与离线校验 | 自动 Runner、CLI/API 适配；现有适配器仅手动说明 |

截图当前只保存为证据，AI Analyzer 不读取图像像素。`RemoteController` 当前通过 ADB `input keyevent` 发键，不能视为 USB IR 驱动。Android 自动化默认使用 uiautomator2，当前不引入 Appium。

## 目录

```text
.
├── AGENTS.md / CLAUDE.md          # Agent 规则与入口
├── README.md / pyproject.toml    # 项目入口、唯一依赖及 pytest 配置
├── .env.example / .gitignore
├── frontend/                    # index.html、css、js/api/components/pages/utils、assets
├── backend/                     # FastAPI 模块化单体
│   ├── main.py
│   ├── api/v1/                  # devices/tasks/reports/defects/settings
│   ├── services/
│   ├── schemas/
│   └── dependencies/
├── automation_core/             # 独立自动化核心
│   ├── bootstrap.py / config.py # 共享装配、路径与配置读取
│   ├── device/                  # adb_client、uia2_client、device_manager
│   ├── remote/                  # ADB 遥控按键；USB IR 待实现
│   ├── navigation/              # 稳定页面指纹
│   ├── evidence/                # Collector、JSONL 审计
│   ├── ai/                      # client/analyzer/executor/recovery/tools/prompts
│   └── models/                  # Pydantic 结构化模型
├── tests/                       # pytest 真实业务场景
│   ├── conftest.py
│   ├── system/
│   ├── ota/                     # 需求占位，暂无测试
│   └── translation/             # 需求占位，暂无测试
├── workflows/                   # settings_workflow、network_workflow
├── agent_harness/               # 研发流程，与运行时 AI 解耦
│   ├── router/ / workflows/ / skills/
│   └── adapters/ / policies/ / schemas/ / evaluations/
├── configs/                     # devices/models/environments/accounts.example.yaml
├── docs/                        # architecture/requirements/designs/test_plans/guides
├── tasks/                       # 研发任务记录；_template 为未执行模板
├── data/                        # baselines/dictionaries/templates
├── runtime/                     # jobs/reports/evidence/logs/state，按需创建、全部忽略
├── scripts/                     # 启动、测试、源码打包、现有密钥配置脚本
└── unit_tests/                  # automation_core/backend/agent_harness/engineering
```

推荐目录按实际能力落地：例如已有文本 LLM 客户端仍命名为 `client.py`，不以 `vlm_client.py` 暗示具备视觉能力；未实现功能以说明文件预留，不创建伪成功代码。

## 模块边界

```text
Frontend ──HTTP──> Backend ──> Workflows ──> Automation Core ──> Android / TV
                                 ↑                 ↑
                              pytest ──────────────┘

Agent Harness ──项目文件、人工触发的工具与验证──> 研发和交付
```

- `automation_core` 不导入后端、pytest、业务流程或 Harness。核心设备层也不依赖 AI。
- `workflows` 组织业务动作；`tests` 负责场景、断言和 fixture 生命周期。
- `backend` 路由经服务与依赖层调用核心；不重新实现设备驱动，也不管理 Agent 内部执行循环。
- `automation_core/ai` 是测试运行时能力；`agent_harness/workflows` 是需求、实现、验证和交付协议，两者职责不同。
- uiautomator2 只有一个直接适配点：`automation_core/device/uia2_client.py`。

完整约束见 [AGENTS.md](AGENTS.md)，路径迁移表见 [架构与迁移说明](docs/architecture/monorepo.md)。

## 安装与本地运行

要求 **Python 3.11+**（模型使用 `StrEnum`），设备操作还需 Android SDK Platform Tools 的 `adb`。以下命令从仓库根目录运行。

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,web]"
python scripts/start_backend.py
```

打开 [本地控制台](http://127.0.0.1:8000/) 和 [API 文档](http://127.0.0.1:8000/docs)。启动服务或打开页面不会主动连接设备或请求 AI；设备列表由用户点击查询。当前服务用于本机，无登录和远程任务调度。

macOS / Linux 激活命令为 `source .venv/bin/activate`，之后使用相同 Python 命令。旧命令 `python -m pip install -r requirements.txt` 仍可用，其依赖来自 `pyproject.toml`，不维护第二份版本清单。

仅运行核心时可 `python -m pip install -e .`；核心离线测试使用 `python -m pip install -e ".[dev]"` 后运行 `python -m pytest unit_tests/automation_core -q`。**完整测试需要同时安装 dev 和 web**。当前交付方式为完整源码目录 / 源码 ZIP 配合 editable 安装；普通 wheel 不包含整套前端、配置、测试与 Harness 文档，尚不是独立部署产物。

## 配置与产物

| 配置源 | 内容 |
| --- | --- |
| `configs/devices.yaml` | ADB 路径、serial 默认值、通信超时、hierarchy 尝试上限 |
| `configs/models.yaml` | 兼容服务地址、模型、凭证环境变量名称、请求与恢复上限 |
| `configs/environments.yaml` | 本机后端 host / port |
| `configs/accounts.example.yaml` | 未来账户能力所需的环境变量名称示例，无账户实现 |
| `automation_core/config.py` | 统一项目资源与运行产物路径 |

环境变量优先：`ANDROID_SERIAL`、`ADB_PATH`、`OPENAI_MODEL`、`OPENAI_BASE_URL`、`AI_TIMEOUT`。模型凭证变量由 `api_key_env` 指定，当前样例是 `DEEPSEEK_API_KEY`。样例保留现有 DeepSeek 配置；模型可用性和权限需在实际环境验证。

`.env.example` 只作说明，程序**不会自动加载 `.env`**。需要时在当前 PowerShell 设置密钥：

```powershell
$secureKey = Read-Host "DeepSeek API Key" -AsSecureString
$env:DEEPSEEK_API_KEY = [System.Net.NetworkCredential]::new("", $secureKey).Password
```

配置按项目根解析，不随当前工作目录变化。可在进程启动前设置 `TV_AUTOMATION_HOME` 指向完整项目资源目录，或用 `TV_AUTOMATION_RUNTIME_DIR` 覆盖产物目录（相对项目根或绝对路径）。默认产物：

- `runtime/evidence/screenshots/`、`runtime/evidence/ui_dump/`：设备证据。
- `runtime/logs/ai_recovery.jsonl`：有界恢复审计。
- `runtime/reports/`：测试报告；`run_tests.py --report` 生成 JUnit XML，后端只索引此目录。
- `runtime/jobs/`、`runtime/state/`：预留运行任务和本机状态；尚无调度服务。

所有 `runtime/` 产物和历史 `reports/` 均被 Git 忽略；原有本机 `reports/` 证据保留，不自动搬运或删除。不得把凭证写入源码、配置样例、日志、报告或任务记录。

## 验证

```powershell
# 默认离线：排除 device 和 ai marker
python -m pytest -q
python -m pytest --collect-only -q

# 查看包括真实设备/AI在内的全部用例，只收集不执行
python -m pytest --collect-only -q -o addopts=""

# 统一测试脚本，生成控制台可索引的报告
python scripts/run_tests.py --report -q

# Harness 文件协议校验，不调用模型和设备
python -m agent_harness.evaluations.validate
```

设备必须开启调试、通过 ADB 授权；uiautomator2 与 ADB 复用同一 serial。系统设置场景还依赖设备上对应的中文菜单，具体前提见 [测试计划](docs/test_plans/README.md)。

```powershell
$env:ANDROID_SERIAL = "你的设备序列号"
python -m pytest tests/system -m "device and not ai" -v
# AI 场景另需配置对应 Provider 凭证
python -m pytest tests/system -m "device and ai" -v
```

无设备导致跳过、marker 排除或服务缺失均不等于设备能力验证通过。重构的实际命令与结果记录于 [验证记录](docs/test_plans/refactor_verification.md)。

不启动 Web 或 pytest 也可装配核心（构造不会连接设备，实际操作才连接）：

```python
from automation_core.bootstrap import create_tv
from workflows.settings_workflow import SettingsFlow

tv = create_tv()
# 以下操作需要真实设备：
# SettingsFlow(tv).open_settings()
# print(tv.current_activity())
```

AI 恢复限定在单个 Step，默认最多 3 步，遵循动作白名单、置信度、页面指纹及重复入口熔断；默认 Agent 工具集不开放任意 ADB shell。详见 [AI 恢复指南](docs/guides/ai_recovery.md)。

## Agent 研发工作流与交付

读取 [Harness 使用说明](agent_harness/README.md) 和 [意图路由](agent_harness/router/intent_router.md)，将 `tasks/_template/` 复制为实际任务目录，选择功能开发、测试开发、测试交付或缺陷评审工作流。由当前 Agent 会话手动执行，按事实记录验证命令与结果。没有安装或声称运行独立 Agent Runner。

源码交付包：

```powershell
python scripts/package_release.py
```

包输出到 `dist/`，仅包含允许的源码、文档、示例配置和模板，不包含虚拟环境、运行产物、真实任务证据或凭证。它不是独立可执行程序；解压后按安装步骤准备 Python 依赖与 ADB。
