"""FastAPI 入口；导入或启动应用不会连接设备或 AI 服务。"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from automation_core.config import PROJECT_ROOT
from backend.api.v1 import defects, devices, reports, settings, tasks


def create_app() -> FastAPI:
    """创建提供只读 API 与同源静态控制台的本地应用。"""
    app = FastAPI(title="Android TV Automation Platform", version="0.1.0")
    for router in (devices.router, tasks.router, reports.router, defects.router, settings.router):
        app.include_router(router, prefix="/api/v1")

    @app.get("/api/v1/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": "local-read-only"}

    frontend_dir = PROJECT_ROOT / "frontend"
    if frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
    return app


app = create_app()
