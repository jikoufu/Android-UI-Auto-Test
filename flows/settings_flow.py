"""Settings-related Android TV flows."""

from devices.tv import AndroidTV


class SettingsFlow:
    def __init__(self, tv: AndroidTV) -> None:
        self.tv = tv

    def open_settings(self) -> None:
        self.tv.adb.run_shell("am start -a android.settings.SETTINGS")

    def is_settings_page(self) -> bool:
        activity = self.tv.current_activity() or ""
        return "settings" in activity.casefold()
