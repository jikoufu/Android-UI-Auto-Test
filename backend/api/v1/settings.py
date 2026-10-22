from fastapi import APIRouter

from automation_core.config import config_path
from backend.schemas import SettingsStatus

router = APIRouter(tags=["settings"])


@router.get("/settings", response_model=SettingsStatus)
def settings_status() -> SettingsStatus:
    """只返回配置文件存在状态，不读取或回传凭证与服务地址。"""
    return SettingsStatus(
        device_config_present=config_path("devices.yaml").is_file(),
        model_config_present=config_path("models.yaml").is_file(),
    )
