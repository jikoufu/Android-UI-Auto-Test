from fastapi import APIRouter, Depends, HTTPException

from backend.dependencies import get_device_service
from backend.schemas import DeviceList
from backend.services.device_service import DeviceService, DeviceUnavailableError

router = APIRouter(tags=["devices"])


@router.get("/devices", response_model=DeviceList)
def list_devices(service: DeviceService = Depends(get_device_service)) -> DeviceList:
    try:
        return service.list_devices()
    except DeviceUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
