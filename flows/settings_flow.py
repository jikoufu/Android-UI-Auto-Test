"""Android TV 系统设置相关业务流程。"""

from devices.tv import AndroidTV


class SettingsFlow:
    def __init__(self, tv: AndroidTV) -> None:
        """保存 Android TV 设备门面。"""
        self.tv = tv

    def open_settings(self) -> None:
        """通过 Android 系统 Intent 打开设置主页。"""
        self.tv.adb.run_shell("am start -a android.settings.SETTINGS")

    def is_settings_page(self) -> bool:
        """根据前台 Activity 判断是否处于设置页面。"""
        activity = self.tv.current_activity() or ""
        return "settings" in activity.casefold()
