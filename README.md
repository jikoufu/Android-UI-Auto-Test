# Android TV 自动化测试框架

本项目基于 Pytest 构建 Android TV 自动化测试框架，提供 ADB、UI 和遥控器的基础封装，并为 AI Agent 的失败分析、工具调用和有限次自动恢复提供扩展接口。

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

## 环境安装

建议 Python 3.10 或更高版本，并安装 Android SDK Platform Tools（提供 `adb`）：

```bash
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

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
