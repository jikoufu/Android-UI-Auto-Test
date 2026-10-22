# 遥控器适配

`controller.py` 中 RemoteController 复用 ADBClient，通过 Android input keyevent 发送按键。USB IR 硬件型号、通信协议和驱动尚待配置与实现。本目录不依赖 uiautomator2，不以 ir_controller.py 的空壳暗示 IR 已可用。
