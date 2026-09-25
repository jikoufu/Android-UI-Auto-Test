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

运行单个文件：

```bash
pytest tests/test_recovery.py -v
```

纯本地测试不需要连接 Android 设备。真实设备测试应使用 `device` marker，并在运行环境已准备好设备后执行。

## AI 配置

AI 参数位于 `config/ai.yaml`。通过环境变量提供凭证和模型：

- `OPENAI_API_KEY`：由 `api_key_env` 指定的 API Key 环境变量。
- `OPENAI_MODEL`：要使用的模型名称。
- `OPENAI_BASE_URL`：可选的 OpenAI 兼容服务地址。
- `AI_TIMEOUT`：可选的请求超时时间（秒）。

Agent 工具调用默认最多 5 次，恢复默认最多 3 步；达到上限、重复动作或需要人工判断时会停止。不要把 API Key 或 Token 写入仓库。

## 开发规范

保持 `tests → flows → devices` 的依赖方向。AI Tool 仅适配既有 `flows/` 或 `devices/` 方法。更多目录规则、安全边界和验证要求见 [AGENTS.md](AGENTS.md)。
