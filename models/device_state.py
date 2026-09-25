"""A point-in-time description of an Android device."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DeviceState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    serial: str = Field(min_length=1)
    connected: bool = True
    model: str | None = None
    manufacturer: str | None = None
    android_version: str | None = None
    current_activity: str | None = None
    captured_at: datetime = Field(default_factory=datetime.now)
