"""依赖组装；不在模块导入时创建设备或 AI 连接。"""

from automation_core.bootstrap import create_adb
from automation_core.config import PROJECT_ROOT, RUNTIME_ROOT
from backend.services.device_service import DeviceService
from backend.services.report_service import ReportService
from backend.services.task_service import TaskService


def get_device_service() -> DeviceService:
    return DeviceService(create_adb)


def get_task_service() -> TaskService:
    return TaskService(PROJECT_ROOT / "tests")


def get_report_service() -> ReportService:
    return ReportService(RUNTIME_ROOT / "reports")
