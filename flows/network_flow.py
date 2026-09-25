"""Network settings flows for Android TV."""

from devices.tv import AndroidTV


class NetworkFlow:
    def __init__(self, tv: AndroidTV) -> None:
        self.tv = tv

    def open_network_settings(self) -> None:
        self.tv.adb.run_shell("am start -a android.settings.WIFI_SETTINGS")

    def is_network_page(self) -> bool:
        activity = (self.tv.current_activity() or "").casefold()
        return "wifi" in activity or "network" in activity
