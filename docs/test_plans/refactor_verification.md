# 本次目录重构验证记录

本轮将现有工作区迁入 Monorepo + 模块化单体 + 独立 Agent Harness。使用 Windows PowerShell、Python 3.12.6；迁移后完整验证使用项目 `.venv`，未连接真实 Android 设备或 AI Provider。

## 实际命令与结果

| 实际执行 | 结果 |
| --- | --- |
| 迁移前 `python -m pytest -q` | 82 passed，4 deselected |
| `.venv/Scripts/python.exe -m pip install -e ".[dev,web]"` | 成功，核心与 Web / 开发依赖已安装到项目虚拟环境 |
| `.venv/Scripts/python.exe -m pytest unit_tests/automation_core -q`（原有测试迁移后） | 82 passed |
| `.venv/Scripts/python.exe -m pytest unit_tests/backend -q` | 19 passed |
| `python -m pytest unit_tests/agent_harness -q` | 20 passed |
| `.venv/Scripts/python.exe -m pytest unit_tests/engineering -q` | 24 passed |
| `.venv/Scripts/python.exe -m pytest -q`（第一次整体验证） | 154 passed，4 deselected，1 warning |
| `.venv/Scripts/python.exe -m pytest --collect-only -q`（第一次整体验证） | 154/158 collected，4 deselected |
| `.venv/Scripts/python.exe scripts/run_tests.py --report -q`（最终增加 NaN / infinity 超时边界后） | **156 passed，4 deselected，1 warning**；JUnit 写入 `runtime/reports/pytest.xml` |
| `.venv/Scripts/python.exe -m pytest --collect-only -q -o addopts=""`（最终） | **160 tests collected**；只收集，不执行设备/AI 用例 |
| `.venv/Scripts/python.exe -X utf8 -m agent_harness.evaluations.validate` | 4 条工作流及任务模板校验通过 |
| 7 个新 SKILL.md 经 skill-creator 的 `quick_validate.py` 检查 | 全部通过 |
| `git diff --check` | 无差异空白错误；Git 提示本机换行符转换 |
| `git check-ignore runtime/logs/ai_recovery.jsonl runtime/evidence/example.xml reports/example.xml dist/example.zip` | 四类生成路径均被忽略 |

最终 156 项通过包含原有 82 项回归、新增 11 项核心装配/依赖边界检查、19 项 API 检查、20 项 Harness 协议检查及 24 项工程脚本检查。

## 其他实际检查

- 前后端子任务在 `127.0.0.1:8001` 启动临时服务并用浏览器验证：首页、CSS、ES Modules、health/settings/defects/reports/tasks/catalog 均正常返回；真实目录显示 4 个场景，报告导航和刷新正常，浏览器无 error/warn。未请求 `/devices`，未访问 AI。验证结束后关闭服务器和浏览器。
- 原先未提交的 `ai/context.py`、`ai/executor.py`、提示词、`devices/adb.py`、`devices/ui.py` 及对应三份测试逐项与迁移前工作区副本对比。除新导入、路径和函数迁移外保留现有实现；两个页面指纹函数在规范化名称后 AST 等价。
- 核心独立进程测试从其他工作目录装配设备对象，验证不导入 Web、pytest、业务 workflows 或 Harness，也不启动设备进程。
- API 测试使用 fake ADB，覆盖真实状态传递、设备缺失、错误脱敏、报告路径越界及下载；这些是离线行为验证。
- 源码打包测试覆盖模板允许范围、凭证/真实任务/运行产物排除、链接和越界路径拒绝、同名包不覆盖。源码 ZIP 不等于独立 EXE，也不代表真实部署验证。

## 限制与待验证项

4 项真实场景被 marker 排除，不能作为设备能力通过。OTA、翻译、USB IR、VLM 图像分析、Web 任务调度、认证、缺陷持久化和自动 Agent Runner 均未实现或未配置，状态见根 README。

当前 Starlette TestClient 使用 httpx 时产生 1 条 `StarletteDeprecationWarning`，建议未来评估 httpx2；本轮未屏蔽警告或为消除提示更改测试客户端。普通 wheel 未提供完整平台资源，部署使用完整源码与 editable 安装。
