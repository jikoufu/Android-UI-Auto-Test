"""Android TV remote-key control through ADB."""

from devices.adb import ADBClient


class RemoteController:
    _KEYS = {
        "BACK", "HOME", "MENU", "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT",
        "DPAD_RIGHT", "DPAD_CENTER", "ENTER", "POWER", "VOLUME_UP",
        "VOLUME_DOWN", "MUTE", "CHANNEL_UP", "CHANNEL_DOWN",
    }

    def __init__(self, adb: ADBClient) -> None:
        self.adb = adb

    def press_key(self, key: str) -> None:
        normalized = key.strip().upper().removeprefix("KEYCODE_")
        if normalized not in self._KEYS:
            raise ValueError(f"Unsupported remote key: {key}")
        self.adb.run_shell(f"input keyevent KEYCODE_{normalized}")
