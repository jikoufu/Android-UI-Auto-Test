"""Convenience facade that groups the device-level Android TV controls."""

from models.device_state import DeviceState
from devices.adb import ADBClient
from devices.remote import RemoteController
from devices.ui import UIDriver


class AndroidTV:
    def __init__(self, adb: ADBClient, remote: RemoteController, ui: UIDriver) -> None:
        self.adb = adb
        self.remote = remote
        self.ui = ui

    def get_device_state(self) -> DeviceState:
        return self.adb.get_device_state()

    def current_activity(self) -> str | None:
        return self.adb.current_activity()
