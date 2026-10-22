# AI Step 恢复与证据

### AI Recoverable Step

`AIExecutor.run_step()` 在单个测试步骤内处理动作、结果校验和受限恢复。正常情况下直接返回动作结果；步骤失败时收集错误、当前 Activity、设备状态和精简的可见 UI 节点，交给 `AIAnalyzer` 返回结构化 `RecoveryAction`，再由 `RecoveryManager` 按置信度门槛和恢复步数上限执行白名单动作。测试只提供目标路径；AI 根据当前页面自行判断返回、滚动或点击哪个可见入口。每次动作后都会重新采集现场和分析，点击目标必须出现在最新可见节点中。

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

`configs/models.yaml` 中一般恢复动作的 `min_recovery_confidence` 默认是 `0.75`，`max_recovery_steps` 默认是 `3`。点击、返回、滚动的默认门槛分别是 `0.70`、`0.55`、`0.45`；点击目标必须是当前 hierarchy 中可见且可点击的完整文字或无障碍描述。具体测试可为较长导航目标设置更高但仍有限的步数。相同页面状态上重复执行同一动作会熔断。无效结构化响应最多纠正重试一次。恢复失败或达到上限时，步骤以 `AIRecoveryError` 失败并要求人工介入。默认工具注册表不开放任意 `adb_shell`。

每次 `run_step()` 维护独立的 Navigation State：稳定页面指纹、`visited_edges`（父页面指纹和入口标签）、当前页的 `blocked_targets_on_current_page` 与 `navigation_stack`。Executor 从可见可点击的 UI 元素生成 `available_navigation_candidates`，交给 AI 选下一步，并在执行前再次校验。点击成功后立即记录边；返回且重新观察到父页面后路径出栈。同一父页面的入口不会重复进入，不同父页面的同名入口仍可分别进入。指纹只使用 Activity 和应用节点的稳定属性，忽略焦点、坐标及系统状态栏。若当前分支无候选、没有可滚动区域或滚动后页面未变化，Context 会提示 AI 返回父页面；Executor 不自动进行深度优先搜索。

设置页面导航示例：测试目标写作“设置 → 我的设备 → 开发者选项”。AI 在“我的设备”页面找不到目标时，可返回设置，检查当前可见页面并按需向下滚动，再从实时层级中选择它判断合适的入口；每次只执行一个动作并根据新现场继续判断。

每次 `AIExecutor.run_step()` 的路径记录会以 UTF-8 JSON Lines 追加到 `runtime/logs/ai_recovery.jsonl`。记录包含最终目标、起点、每轮页面 Activity、可见文字和可滚动容器、AI 返回的判断步骤摘要、理由、证据、动作和置信度、分析请求耗时、页面指纹、阻塞目标、已探索边数量、导航路径、实际执行动作、校验结果和最终状态。重复边拒绝记录为 `action_rejected` 且 `reason=visited_edge`。pytest 运行时也会即时打印每轮 AI 决策摘要与耗时。这里记录的是面向用户的简短判断依据，不是模型隐藏的内部推理文本。使用 `run_id` 可筛选一次运行。日志不保存 API Key 或截图像素。

PowerShell 中可按测试步骤查看 AI 路径：

```powershell
Get-Content runtime/logs/ai_recovery.jsonl | ForEach-Object { $_ | ConvertFrom-Json } |
  Where-Object { $_.step -like '*test_ai_navigates_from_my_device*' } |
  Format-List timestamp, run_id, event, current_activity, analysis_duration_ms, decision_steps, reason, evidence, suggested_action, target_text, scroll_direction, confidence, accepted, action, passed
```

首次失败和最终失败分别保存截图；中间恢复轮次默认不截图。设备状态首次成功获取后由 Collector 缓存，每轮仍独立采集 Activity 与 UI hierarchy，单项采集超时不会阻止其他证据。uiautomator2 hierarchy RPC 遇到 `RemoteDisconnected`、连接重置、短暂超时或无效 JSON 响应时，`UIDriver` 清理旧连接，沿用当前 ADB serial 重连，并重试一次；仍失败则调用已有 `ADBClient.dump_ui()` 降级采集。默认最多两次 u2 尝试和一次 ADB 降级，由 `configs/devices.yaml` 限定 u2 尝试次数。全部失败才抛 `DeviceTransportError`，不把通信故障交给 LLM 决策。每轮 trace 记录 hierarchy 来源和 transport recovery 结果；驱动自愈不占用 AI 恢复步数。`refind_element` 会重新执行原测试动作，因此不降低其通用置信度门槛。当前 AI Analyzer 默认只读取最多 80 个可见 UI 节点，不读取原始 XML 或截图像素；XML 和截图作为测试证据保存。DeepSeek 恢复请求默认关闭 thinking，并将输出限制为 800 tokens；这些参数和置信度门槛均在 `configs/models.yaml` 配置。

真实 AI 设备导航场景位于 `tests/system/test_ai_settings_navigation.py`，需要连接 Android 设备并配置 `DEEPSEEK_API_KEY` 后单独运行。该场景从“我的设备”误入路径开始，要求 AI 逐项导航至开发者选项并验证页面中的 USB 调试设置。


核心入口为 `automation_core/ai/executor.py`，现场采集在 `automation_core/evidence/collector.py`，页面指纹在 `automation_core/navigation/page_detector.py`。这些运行时能力与 `agent_harness/` 的研发文件协议相互独立。本文描述已实现的代码约束；本轮重构仅使用 fake/mock 验证，未验证真实设备或 AI Provider。
