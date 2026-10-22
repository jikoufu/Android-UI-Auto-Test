from fastapi import APIRouter, Depends, HTTPException

from backend.dependencies import get_task_service
from backend.schemas import TaskCatalog
from backend.services.task_service import CatalogUnavailableError, TaskService

router = APIRouter(tags=["tasks"])


@router.get("/tasks/catalog", response_model=TaskCatalog)
def task_catalog(service: TaskService = Depends(get_task_service)) -> TaskCatalog:
    try:
        return service.catalog()
    except CatalogUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
