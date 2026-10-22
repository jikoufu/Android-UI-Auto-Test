from fastapi import APIRouter

from backend.schemas import CapabilityStatus
from backend.services.defect_service import get_defect_status

router = APIRouter(tags=["defects"])


@router.get("/defects", response_model=CapabilityStatus)
def defect_status() -> CapabilityStatus:
    return get_defect_status()
