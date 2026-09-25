# Android TV 自动化测试框架

本项目是基于 Pytest 的 Android TV 自动化测试框架。设备层提供 ADB、uiautomator2 和独立的遥控器控制接口，并提供 AI Agent 扩展能力，用于测试失败分析、设备状态理解和有限次数的自动恢复。

项目当前只面向 Android / Android TV，使用 Python + Pytest。uiautomator2 可直接集成 Python，并与现有 ADB、Remote 和 AI Tools 架构保持轻量。当前没有跨平台、Appium Grid 或 Hybrid/WebView 需求，因此当前阶段不引入 Appium；未来需求变化时再评估。

## 项目目录

```text
.
├── ai/
│   ├── agent.py                 # 有工具调用上限的 Agent Loop
│   ├── analyzer.py              # 解析结构化失败分析结果
│   ├── client.py                # OpenAI 兼容接口客户端
│   ├── context.py               # 收集步骤失败时的设备与 UI 现场
│   ├── executor.py              # Step 级 AI 分析和有限恢复
│   ├── recovery.py              # 有上限且可熔断的恢复策略
│   ├── prompts/                 # AI 提示词
│   └── tools/                   # ADB、遥控器、UI、设备状态适配器
├── config/                      # AI 和设备的非敏感配置
├── data/                        # 测试数据
├── devices/                     # ADB、Android TV、遥控器和 UI 驱动
│   ├── adb.py                   # ADB 系统操作与设备诊断
│   ├── ui.py                    # 基于 uiautomator2 的 UI 自动化
│   ├── remote.py                # 遥控器按键接口（当前通过 ADB 发送 keyevent）
│   └── tv.py                    # Android TV 设备能力的组合入口
├── flows/                       # Settings、Network 等业务流程
├── models/                      # Pydantic 结构化模型
├── reports/                     # 本地日志、截图和 UI dump 输出
├── tests/                       # Pytest 用例
├── unit_tests/                  # 使用 fake/mock 的纯单元测试
├── utils/                       # 通用配置等辅助工具
├── AGENTS.md                    # AI Coding Agent 项目规范
├── conftest.py                  # Pytest fixtures
├── pytest.ini                   # Pytest 配置
└── requirements.txt             # Python 依赖
```

## 架构

设备测试按以下方向组织：

```text
tests → flows → devices
```

AI 通过适配器调用已有的流程或设备能力：

```text
AI Agent → AI Tools → flows / devices
```

测试断言放在 `tests/`，业务动作放在 `flows/`，真实设备操作集中在 `devices/`。工具适配器不实现 ADB 或遥控器驱动。

```text
Pytest Tests
     ↓
   Flows
     ↓
   Devices
   ├── ADB：系统命令和设备诊断
   ├── UI：UIDriver → uiautomator2
   └── RemoteController：遥控器按键（当前由 ADB keyevent 实现）
```

AI Agent 通过工具适配器调用已存在的业务流程或设备能力：

```text
AI Agent
   ↓
AI Tools
   ↓
flows / devices
```

正式 UI 自动化由 `devices/ui.py` 中的 `UIDriver` 统一封装 uiautomator2。它提供元素定位、点击、等待、UI hierarchy dump 和截图；已有 XML 可用于离线调试和证据分析。ADB 继续处理系统命令和设备诊断，遥控器接口与 UI 驱动保持独立。当前 `RemoteController` 使用 ADB `input keyevent`，USB IR 硬件适配仍待配置。

## 环境安装

建议 Python 3.10 或更高版本，并安装 Android SDK Platform Tools（提供 `adb`）。uiautomator2 由 `requirements.txt` 管理：

```bash
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

`pip install -r requirements.txt` 会一并安装 uiautomator2。首次使用时，确保目标 Android TV 已开启 USB 调试或已通过 ADB 网络连接；连接多台设备时设置 `ANDROID_SERIAL`，ADB 和 UIDriver 会复用同一设备 serial。

macOS / Linux：

```bash
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

连接一台设备即可自动选择。连接多台设备时请设置 `ANDROID_SERIAL`；需要自定义 ADB 路径时设置 `ADB_PATH`。

## 执行测试

```bash
pytest
```

收集测试用例：

```bash
pytest --collect-only -q
```

运行单个纯单元测试文件：

```bash
pytest unit_tests/test_recovery.py -v
```

纯单元测试统一放在 `unit_tests/`，不需要连接 Android 设备或配置 AI Provider。`tests/` 保留业务集成与真实设备场景；真实设备测试应使用 `device` marker，并在运行环境已准备好设备后执行。

## AI 配置

AI 已配置为使用 DeepSeek OpenAI 兼容接口：`https://api.deepseek.com`，模型为 `deepseek-flash`。API Key 通过 `DEEPSEEK_API_KEY` 环境变量读取，不写入仓库。

在 PowerShell 当前窗口中运行以下命令，会隐藏输入内容并设置环境变量：

```powershell
$secureKey = Read-Host "请输入 DeepSeek API Key" -AsSecureString
$env:DEEPSEEK_API_KEY = [System.Net.NetworkCredential]::new("", $secureKey).Password
```

然后在同一个 PowerShell 窗口中运行 AI 测试。关闭窗口后需要重新输入密钥。也可以用环境变量覆盖模型或接口地址：

- `OPENAI_MODEL`：可选的模型名称覆盖值。
- `OPENAI_BASE_URL`：可选的 OpenAI 兼容服务地址覆盖值。
- `AI_TIMEOUT`：可选的请求超时时间（秒）。

Agent 工具调用默认最多 5 次，恢复默认最多 3 步；达到上限、重复动作或需要人工判断时会停止。不要把 API Key 或 Token 写入仓库。

### AI Recoverable Step

`AIExecutor.run_step()` 在单个测试步骤内处理动作、结果校验和受限恢复。正常情况下直接返回动作结果；步骤失败时收集错误、当前 Activity、设备状态和 UI hierarchy，交给 `AIAnalyzer` 返回结构化 `RecoveryAction`，再由 `RecoveryManager` 按置信度门槛和恢复步数上限执行白名单动作。测试只提供目标路径；AI 根据当前页面自行判断返回、滚动或点击哪个可见入口。每次动作后都会重新采集现场和分析，点击目标必须出现在最新 UI hierarchy 中。

```python
ai_executor.run_step(
    name="进入我的设备",
    action=lambda: ui.click_text("我的设备", timeout=10),
    verify=lambda: ui.exists("text", "设备名称", timeout=3),
)
```

执行链路：

```text
Step → Failure → Context Collection → AI Analyzer → RecoveryAction → Bounded Recovery
```

`config/ai.yaml` 中一般恢复动作的 `min_recovery_confidence` 默认是 `0.75`，`max_recovery_steps` 默认是 `3`。只关闭当前页的返回和不点击菜单的滚动最低置信度为 `0.4`；点击动作最低为 `0.4`，且目标必须是 AI 根据当前现场选出的可见完整文字。具体测试可为较长导航目标设置更高但仍有限的步数。连续相同动作会熔断；无效结构化响应最多纠正重试一次。恢复失败或达到上限时，步骤以 `AIRecoveryError` 失败并要求人工介入。默认工具注册表不开放任意 `adb_shell`。

设置页面导航示例：测试目标写作“设置 → 我的设备 → 开发者选项”。AI 在“我的设备”页面找不到目标时，可返回设置，检查当前可见页面并按需向下滚动，再从实时层级中选择它判断合适的入口；每次只执行一个动作并根据新现场继续判断。

每次 `AIExecutor.run_step()` 的路径记录会以 UTF-8 JSON Lines 追加到 `reports/logs/ai_recovery.jsonl`。记录包含最终目标、起点、每轮页面 Activity、可见文字和可滚动容器、AI 返回的判断步骤摘要、理由、证据、动作和置信度、实际执行动作、校验结果和最终状态；pytest 运行时也会即时打印每轮 AI 决策摘要。这里记录的是面向用户的简短判断依据，不是模型隐藏的内部推理文本。使用 `run_id` 可筛选一次运行。日志不保存 API Key 或截图像素。

PowerShell 中可按测试步骤查看 AI 路径：

```powershell
Get-Content reports/logs/ai_recovery.jsonl | ForEach-Object { $_ | ConvertFrom-Json } |
  Where-Object { $_.step -like '*test_ai_navigates_from_my_device*' } |
  Format-List timestamp, run_id, event, current_activity, decision_steps, reason, evidence, suggested_action, target_text, scroll_direction, confidence, accepted, action, passed
```

失败现场会保存 UI hierarchy 和截图路径。当前 AI Analyzer 会读取受长度限制的 UI XML 文本，但不会读取截图像素；截图目前只作为测试证据保存，视觉分析留待后续迭代。

真实 AI 设备导航场景位于 `tests/test_ai_settings_navigation.py`，需要连接 Android 设备并配置 `DEEPSEEK_API_KEY` 后单独运行。该场景从“我的设备”误入路径开始，要求 AI 逐项导航至开发者选项并验证页面中的 USB 调试设置。

## 开发规范

保持 `tests → flows → devices` 的依赖方向。AI Tool 仅适配既有 `flows/` 或 `devices/` 方法。更多目录规则、安全边界和验证要求见 [AGENTS.md](AGENTS.md)。
