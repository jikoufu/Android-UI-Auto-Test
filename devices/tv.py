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

    def get_device_state(self, include_activity: bool = True) -> DeviceState:
        """返回当前 Android TV 的设备状态。"""
        return self.adb.get_device_state(include_activity=include_activity)

    def get_static_device_state(self, timeout: float = 3) -> DeviceState:
        """返回可缓存的静态设备属性，使用短 ADB 超时。"""
        return self.adb.get_static_device_state(timeout=timeout)

    def current_activity(self, timeout: float | None = None) -> str | None:
        """返回当前前台 Activity 名称。"""
        return self.adb.current_activity(timeout=timeout)
