# AGENTS.md

## 架构约束（所有 Agent 必须遵守）

1. `automation_core` 独立于前后端，不得导入 `backend`、`frontend`、`workflows`、`tests`、`pytest` 或 `agent_harness`；不启动 Web 服务也能运行核心。
2. `tests` 负责场景、断言和证据验证，不重复实现设备能力；fixture 只管理生命周期，共享装配使用 `automation_core/bootstrap.py`。
3. `backend` 负责 API、服务边界与任务管理，不实现 ADB/UIA2/VLM 驱动。当前只读查询已实现，调度、认证和缺陷持久化待开发；路由通过 services/dependencies 使用核心。
4. `workflows` 负责 TV 测试业务；`agent_harness` 负责研发、验证与交付协议。Harness 当前为手动文件工作流，不是设备 AI 恢复、模型接入或自动 Runner。
5. 每次修改后必须提供变更摘要、实际验证命令与结果、未验证风险；没有执行的测试、未连接的设备和未配置的服务不得写成通过。

依赖边界：`frontend → backend → workflows / automation_core`；`tests → workflows / automation_core`；`workflows → automation_core`。禁止反向依赖和循环依赖。Harness 通过项目文件和工具协作，业务代码不导入 Harness。

## 新目录和配置原则

- `frontend/`：静态页面、API 客户端与展示组件；只通过后端 API 访问平台。
- `backend/api/v1/`、`services/`、`schemas/`、`dependencies/`：分别负责 HTTP、应用服务、数据契约和依赖装配。
- `automation_core/device/`：ADB、UIA2、设备组合；`remote/`：遥控器；`navigation/`：页面识别；`evidence/`：现场与审计；`ai/`：运行时 AI；`models/`：结构化结果。
- `agent_harness/router/`、`workflows/`、`skills/`、`adapters/`、`policies/`、`schemas/`、`evaluations/`：研发协议。通用技能不得硬编码 TV 业务；项目专属规则从根 AGENTS 和 docs 引用。每种规则、技能和配置只维护一个规范源。
- `configs/` 为非敏感配置唯一目录；Python 依赖和 pytest 配置唯一入口为 `pyproject.toml`。`requirements.txt` 只引用该配置。
- `automation_core/config.py` 统一解析项目路径；默认以仓库根为基准，禁止依赖当前工作目录寻找配置或提示词。需要外部资源目录时设置 `TV_AUTOMATION_HOME`。
- `runtime/{jobs,reports,evidence,logs,state}/` 为按需创建且全部忽略的产物；`TV_AUTOMATION_RUNTIME_DIR` 可覆盖根路径。历史 `reports/` 仅保留本机旧证据并忽略，不再作为默认输出。
- `docs/{architecture,requirements,designs,test_plans,guides}/` 保存项目知识；`tasks/` 保存研发任务记录，不混入设备 job 状态。提交任务记录前移除敏感证据和凭证。
- `scripts/` 提供本地启动、测试和源码打包；打包必须排除凭证、运行产物、虚拟环境和真实测试数据。
- 当前交付采用源码/ZIP + editable 安装；OTA、翻译、VLM 图像定位、USB IR 尚无完整实现，不创建伪成功占位函数或测试。

## 项目定位

本项目是采用 Monorepo + 模块化单体 + 独立 Agent Harness 的 Android TV 自动化平台，核心技术包括 Python、Pytest、ADB、uiautomator2、Remote / USB IR、LLM 和 AI Agent。设备驱动、AI 服务和真实设备测试必须明确标为已实现或待配置，不能虚报可用状态。

当前项目只面向 Android / Android TV，主要使用 Python + Pytest。uiautomator2 可直接集成 Python，并与现有 ADB、Remote 和 AI Tools 架构保持轻量。当前没有 Android+iOS 跨平台、Appium Grid 或 Hybrid/WebView 等需求，因此当前阶段不引入 Appium；未来需求变化时再重新评估。

## 目录职责

- `tests/`：Pytest 场景和断言，保持业务可读，不堆底层设备操作。
- `unit_tests/{automation_core,backend,agent_harness,engineering}/`：不连接真实设备或 AI Provider 的平台测试，使用 fake/mock 隔离依赖。
- 新增或修改 `tests/` 用例时，遵循项目内 `.agents/skills/pytest-test-writer/SKILL.md` 的测试函数编写约定。
- `workflows/`：完整业务动作和测试流程，可以调用 `automation_core/device/`。
- `automation_core/device/`：ADB、Android TV、UI、截图和 UIAutomator 等真实设备操作；遥控器在 `automation_core/remote/`。
- `automation_core/ai/`：LLM 客户端、Agent 编排、失败分析、恢复策略和提示词。
- `automation_core/ai/tools/`：将核心已有的 device / remote 能力适配成运行时 Agent 工具，不实现设备驱动，不反向导入 workflows。
- `automation_core/models/`：使用 Pydantic 定义的 AI 结果、设备状态和恢复结果。
- `configs/`：非敏感运行配置。API Key、密码和 Token 只能通过环境变量或本机密钥存储提供。
- `data/`：测试输入数据；不要放真实凭证或生产敏感数据。
- `runtime/`：运行生成的日志、截图和 UI dump，必须被 Git 忽略。

## UI 自动化

- Android UI 自动化默认使用 `uiautomator2`。
- `automation_core/device/uia2_client.py` 是项目中唯一直接依赖 uiautomator2 的适配层。
- `tests/`、`workflows/`、`automation_core/ai/tools/` 不得直接导入 uiautomator2。
- 依赖方向：

```text
tests → workflows → automation_core/device/uia2_client.py → uiautomator2
AI Agent → automation_core/ai/tools/ui_tool.py → automation_core/device/uia2_client.py → uiautomator2
```

- ADB 不负责正常 UI 元素自动化；ADB 主要负责系统能力、诊断、日志和补充控制。
- Remote / USB IR 是独立设备能力，不与 uiautomator2 合并。
- 当前 `RemoteController` 通过 ADB `input keyevent` 发送按键；USB IR 硬件适配尚待配置，文档和测试结果必须如实说明。
- `UIDriver.dump_ui()` 使用 uiautomator2 采集实时 hierarchy；保存的 XML 可供离线调试和证据分析。
- 当前阶段不引入 Appium。只有未来出现跨平台、Appium Grid 或 Hybrid/WebView 等明确需求时，才重新评估。

## 依赖方向

```text
tests → workflows → automation_core/device
运行时 AI Agent → automation_core/ai/tools → automation_core/device / remote
```

- `automation_core/device/` 不得导入 `workflows/` 或 `tests/`。
- `workflows/` 不得导入 `tests/` 或 `automation_core/ai/`。
- `automation_core/ai/tools/` 只能调用核心已有的 device / remote 能力，不能导入 workflows 或复制底层驱动。
- 不允许反向依赖或循环依赖。

## 开发原则

1. 优先扩展现有代码，不重复造轮子。
2. 保持实现简单；不引入无意义抽象或大规模重构。
3. 测试用例表达业务场景，底层操作统一放在 `automation_core/device/`。
4. 运行时 AI Tool 只是核心设备能力的适配层，不重新实现驱动。
5. 配置、超时和重试次数集中管理，并保留合理的默认上限。
6. 不得把 API Key、密码或 Token 写入代码、配置样例、日志或测试报告。
7. AI 输出优先解析为 Pydantic 结构化模型，不依赖任意字符串猜测状态。
8. Agent Tool 调用和恢复循环必须设最大次数；默认自动恢复最多 3 步。
9. 重复恢复动作应熔断；失败、证据不足或超过上限时停止并请求人工介入。
10. 外部进程和网络请求必须有超时，并给出不泄漏凭证的错误信息。
11. 保持 Python 类型标注，变更后运行相关 Pytest 验证。
12. 设备相关测试必须标记 `device`，并在文档中说明设备要求；不得把设备缺失描述为测试通过。

## AI Step 恢复规则

- AI 自动恢复必须发生在单个 Step 内；不得在整个 Pytest 测试已经失败后再尝试恢复。
- 所有自动恢复必须遵循配置的最大步数和最小 confidence threshold，并只执行明确列出的白名单动作。
- 测试应向 AI 提供目标路径和当前设备证据，不预先指定菜单路径。AI 可在有限步数内返回、纵向滚动或点击；点击目标必须出现在本次失败现场采集到的可见 UI hierarchy 中，每次动作后重新分析，不能猜测或跳过层级。
- 恢复超过上限、动作重复、置信度不足、AI 请求失败或证据不足时停止自动操作，并抛出清晰错误请求人工介入。
- Prompt 只说明导航规则；Executor 在每次 `run_step()` 内维护页面指纹、已探索边和导航路径，过滤候选并在点击前强制拒绝同一父页面下的重复入口。不同父页面的同名入口可分别探索。
- 页面指纹只使用当前 Activity 及应用节点的稳定文字、无障碍描述、资源 ID、可点击和可滚动属性；不得使用焦点、坐标或系统状态栏动态内容。
- 失败现场应尽可能保存当前 Activity、设备状态、UI hierarchy 和截图；截图当前只作为证据保存，不能声称 AI Analyzer 已读取图像内容。
- 设备状态在一次 Collector 生命周期内缓存；首次失败和最终失败截图，中间导航轮次默认只采集 Activity 与 UI hierarchy。单项采集失败不得阻止其他证据采集。
- 设备通信异常优先由 `automation_core/device/` 层有限自愈，不交给 LLM 判断；RemoteDisconnected、连接重置和短暂 transport timeout 最多重连并重试一次，再尝试已有 ADB hierarchy 降级。禁止无限重连；全部失败时抛出清晰的 `DeviceTransportError`。
- `AIExecutor` 的审计日志写入 `runtime/logs/ai_recovery.jsonl`，需记录 AI 返回的理由、证据、置信度、当前页面摘要和执行结果；不得记录 API Key 或 Token。
- 默认设备 Agent Tool Registry 不得注册任意 `adb_shell`。`adb_shell_tool()` 可保留，但不得加入默认工具集。
- `tests/` 不直接创建或调用 AI Provider；AI 失败分析与 Step 恢复必须通过 `automation_core/ai/analyzer.py` 和 `automation_core/ai/executor.py`。
- `automation_core/device/` 不得依赖 AI，也不负责 AI 分析或恢复策略。

## 验证

使用项目虚拟环境（PowerShell：`& .venv/Scripts/python.exe -m pytest ...`）。完整开发依赖：`python -m pip install -e ".[dev,web]"`。

- 收集用例：`pytest --collect-only -q`
- 运行本地测试：`pytest -q`
- 有设备的场景需按 `ANDROID_SERIAL` 选择设备，并运行对应 `device` 测试。
- 记录实际运行的命令和结果，不声称未运行或未连接的能力已经验证。

- 架构边界由 `unit_tests/automation_core/test_architecture.py` 检查。
- Harness 文件协议：`python -m agent_harness.evaluations.validate`。
- 全量收集（包括不执行的设备/AI 场景）：`python -m pytest --collect-only -q -o addopts=""`。
