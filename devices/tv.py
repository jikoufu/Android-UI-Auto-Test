"""组合 Android TV 的 ADB、遥控器和 UI 设备能力。"""

from models.device_state import DeviceState
from devices.adb import ADBClient
from devices.remote import RemoteController
from devices.ui import UIDriver


class AndroidTV:
    def __init__(self, adb: ADBClient, remote: RemoteController, ui: UIDriver) -> None:
        """保存各设备驱动，供业务流程统一调用。"""
        self.adb = adb
        self.remote = remote
        self.ui = ui

    def get_device_state(self) -> DeviceState:
        """返回当前 Android TV 的设备状态。"""
        return self.adb.get_device_state()

    def current_activity(self) -> str | None:
        """返回当前前台 Activity 名称。"""
        return self.adb.current_activity()
