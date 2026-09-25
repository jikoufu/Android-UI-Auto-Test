"""Android TV 网络设置相关业务流程。"""

from devices.tv import AndroidTV


class NetworkFlow:
    def __init__(self, tv: AndroidTV) -> None:
        """保存 Android TV 设备门面。"""
        self.tv = tv

    def open_network_settings(self) -> None:
        """通过 Android 系统 Intent 打开 Wi-Fi 设置页。"""
        self.tv.adb.run_shell("am start -a android.settings.WIFI_SETTINGS")

    def is_network_page(self) -> bool:
        """根据前台 Activity 判断是否处于网络设置页。"""
        activity = (self.tv.current_activity() or "").casefold()
        return "wifi" in activity or "network" in activity
