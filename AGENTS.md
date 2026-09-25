# AGENTS.md

## 项目定位

本项目是 Android TV 自动化测试框架，核心技术包括 Python、Pytest、ADB、uiautomator2、Remote / USB IR、LLM 和 AI Agent。设备驱动、AI 服务和真实设备测试必须明确标为已实现或待配置，不能虚报可用状态。

当前项目只面向 Android / Android TV，主要使用 Python + Pytest。uiautomator2 可直接集成 Python，并与现有 ADB、Remote 和 AI Tools 架构保持轻量。当前没有 Android+iOS 跨平台、Appium Grid 或 Hybrid/WebView 等需求，因此当前阶段不引入 Appium；未来需求变化时再重新评估。

## 目录职责

- `tests/`：Pytest 场景和断言，保持业务可读，不堆底层设备操作。
- 新增或修改 `tests/` 用例时，遵循项目内 `.agents/skills/pytest-test-writer/SKILL.md` 的测试函数编写约定。
- `flows/`：完整业务动作和测试流程，可以调用 `devices/`。
- `devices/`：ADB、Android TV、遥控器、UI、截图和 UIAutomator 等真实设备操作。
- `ai/`：LLM 客户端、Agent 编排、失败分析、恢复策略和提示词。
- `ai/tools/`：将 `flows/` 或 `devices/` 的现有能力适配成 Agent 可调用工具，不实现设备驱动。
- `models/`：使用 Pydantic 定义的 AI 结果、设备状态和恢复结果。
- `config/`：非敏感运行配置。API Key、密码和 Token 只能通过环境变量或本机密钥存储提供。
- `data/`：测试输入数据；不要放真实凭证或生产敏感数据。
- `utils/`：设备无关且可复用的基础工具，不放 ADB、遥控器、TV 流程或 Agent 逻辑。
- `reports/`：运行生成的日志、截图和 UI dump，必须被 Git 忽略。

## UI 自动化

- Android UI 自动化默认使用 `uiautomator2`。
- `devices/ui.py` 是项目中唯一直接依赖 uiautomator2 的适配层。
- `tests/`、`flows/`、`ai/tools/` 不得直接导入 uiautomator2。
- 依赖方向：

```text
tests → flows → devices/ui.py → uiautomator2
AI Agent → ai/tools/ui_tool.py → devices/ui.py → uiautomator2
```

- ADB 不负责正常 UI 元素自动化；ADB 主要负责系统能力、诊断、日志和补充控制。
- Remote / USB IR 是独立设备能力，不与 uiautomator2 合并。
- 当前 `RemoteController` 通过 ADB `input keyevent` 发送按键；USB IR 硬件适配尚待配置，文档和测试结果必须如实说明。
- `UIDriver.dump_ui()` 使用 uiautomator2 采集实时 hierarchy；保存的 XML 可供离线调试和证据分析。
- 当前阶段不引入 Appium。只有未来出现跨平台、Appium Grid 或 Hybrid/WebView 等明确需求时，才重新评估。

## 依赖方向

```text
tests → flows → devices
AI Agent → ai/tools → flows / devices
```

- `devices/` 不得导入 `flows/` 或 `tests/`。
- `flows/` 不得导入 `tests/` 或 `ai/`。
- `ai/tools/` 只能调用已有的 `flows/` 或 `devices/` 能力，不能复制底层驱动。
- 不允许反向依赖或循环依赖。

## 开发原则

1. 优先扩展现有代码，不重复造轮子。
2. 保持实现简单；不引入无意义抽象或大规模重构。
3. 测试用例表达业务场景，底层操作统一放在 `devices/`。
4. AI Tool 只是流程或设备能力的适配层，不重新实现驱动。
5. 配置、超时和重试次数集中管理，并保留合理的默认上限。
6. 不得把 API Key、密码或 Token 写入代码、配置样例、日志或测试报告。
7. AI 输出优先解析为 Pydantic 结构化模型，不依赖任意字符串猜测状态。
8. Agent Tool 调用和恢复循环必须设最大次数；默认自动恢复最多 3 步。
9. 重复恢复动作应熔断；失败、证据不足或超过上限时停止并请求人工介入。
10. 外部进程和网络请求必须有超时，并给出不泄漏凭证的错误信息。
11. 保持 Python 类型标注，变更后运行相关 Pytest 验证。
12. 设备相关测试必须标记 `device`，并在文档中说明设备要求；不得把设备缺失描述为测试通过。

## 验证

- 收集用例：`pytest --collect-only -q`
- 运行本地测试：`pytest -q`
- 有设备的场景需按 `ANDROID_SERIAL` 选择设备，并运行对应 `device` 测试。
- 记录实际运行的命令和结果，不声称未运行或未连接的能力已经验证。
