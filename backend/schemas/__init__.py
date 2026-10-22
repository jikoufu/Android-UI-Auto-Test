"""平台 API 的公开结构；不包含运行凭证或绝对文件路径。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class DeviceSummary(BaseModel):
    serial: str
    state: str


class DeviceList(BaseModel):
    devices: list[DeviceSummary]
    source: Literal["adb"] = "adb"


class CapabilityStatus(BaseModel):
    status: Literal["implemented", "not_implemented", "not_verified"]
    detail: str


class TaskSummary(BaseModel):
    id: str
    title: str


class TaskCatalog(BaseModel):
    scenarios: list[TaskSummary]
    scheduler: CapabilityStatus


class ReportSummary(BaseModel):
    path: str
    size_bytes: int
    modified_at: datetime


class ReportList(BaseModel):
    reports: list[ReportSummary]
    truncated: bool = False


class SettingsStatus(BaseModel):
    mode: Literal["local-read-only"] = "local-read-only"
    device_config_present: bool
    model_config_present: bool
    ai_connection: Literal["not_verified"] = "not_verified"
    usb_ir: Literal["not_configured"] = "not_configured"
