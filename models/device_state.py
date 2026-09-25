"""记录某一时刻的 Android 设备信息。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DeviceState(BaseModel):
    """设备连接标识、系统信息和当前 Activity。"""
    model_config = ConfigDict(extra="forbid")

    serial: str = Field(min_length=1)
    connected: bool = True
    model: str | None = None
    manufacturer: str | None = None
    android_version: str | None = None
    current_activity: str | None = None
    captured_at: datetime = Field(default_factory=datetime.now)
