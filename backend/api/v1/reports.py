from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from backend.dependencies import get_report_service
from backend.schemas import ReportList
from backend.services.report_service import ReportNotFoundError, ReportService, ReportsUnavailableError

router = APIRouter(tags=["reports"])


@router.get("/reports", response_model=ReportList)
def list_reports(
    limit: int = Query(default=100, ge=1, le=200),
    service: ReportService = Depends(get_report_service),
) -> ReportList:
    try:
        return service.list_reports(limit)
    except ReportsUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/reports/{report_path:path}")
def download_report(report_path: str, service: ReportService = Depends(get_report_service)) -> FileResponse:
    try:
        path = service.resolve_report(report_path)
    except (ReportNotFoundError, OSError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="报告不存在。") from exc
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")
