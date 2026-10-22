"""适配 automation_core 已有的 ADB 设备枚举能力。"""

from collections.abc import Callable

from automation_core.device.adb_client import ADBClient
from backend.schemas import DeviceList, DeviceSummary


class DeviceUnavailableError(RuntimeError):
    """设备查询不可用；公开错误不回显底层命令或配置。"""


class DeviceService:
    def __init__(self, adb_factory: Callable[[], ADBClient]) -> None:
        self._adb_factory = adb_factory

    def list_devices(self) -> DeviceList:
        """只有显式查询时才创建客户端并运行有超时的 ADB 枚举。"""
        try:
            devices = self._adb_factory().list_devices()
        except Exception as exc:
            raise DeviceUnavailableError("ADB 设备查询不可用，请检查本机 ADB 与设备配置。") from exc
        return DeviceList(devices=[DeviceSummary(serial=serial, state=state) for serial, state in devices])
