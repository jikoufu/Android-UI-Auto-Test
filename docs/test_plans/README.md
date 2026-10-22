# 测试分层与设备前提

`unit_tests/` 使用 fake/mock 验证核心、API、Harness 和工程脚本，不连接 Android 设备或 AI Provider。默认 pytest 排除 device 与 ai 标记。

`tests/system/` 有四个真实场景：设备基本操作、设备名称导航、AI 失败现场分析、AI 开发者选项导航。测试需已授权的 ADB 设备、可运行 uiautomator2、适配的系统设置页。后三个依赖中文“我的设备”“设备名称”等菜单，AI 导航目标需存在开发者选项 / USB 调试。设置操作会改变前台页面，请选择测试设备。

AI 场景还需 configs/models.yaml 所指 Provider 与密钥。使用 ANDROID_SERIAL 选定设备。仅 smoke 场景沿用无设备时显式 skip 的既有行为；skip 和 deselected 都不是通过。

`tests/ota/`、`tests/translation/` 暂存需求说明，不包含占位通过用例。实际验证记录见 `refactor_verification.md`。
