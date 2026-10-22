"""平台 API 离线测试；不连接真实设备或模型服务。"""

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from automation_core.device.adb_client import ADBClient, ADBError
from backend import dependencies
from backend.api.v1 import settings
from backend.dependencies import get_device_service, get_report_service, get_task_service
from backend.main import create_app
from backend.services.device_service import DeviceService
from backend.services.report_service import ReportNotFoundError, ReportService
from backend.services.task_service import TaskService


@pytest.fixture
def client() -> Iterator[TestClient]:
    """每个测试使用独立应用，方便隔离依赖覆盖。"""
    with TestClient(create_app()) as instance:
        yield instance


def test_startup_and_health_do_not_create_device_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """验证应用启动和健康检查不连接设备。"""
    # Step 1：禁止设备客户端创建，创建独立本地应用。
    factory = Mock(side_effect=AssertionError("设备客户端不应在启动时创建"))
    monkeypatch.setattr(dependencies, "create_adb", factory)

    # Step 2：请求健康状态与静态首页，并确认未访问设备。
    with TestClient(create_app()) as test_client:
        assert test_client.get("/api/v1/health").json() == {"status": "ok", "mode": "local-read-only"}
        assert "测试工作空间" in test_client.get("/").text
    factory.assert_not_called()


def test_devices_preserve_real_adb_states(client: TestClient) -> None:
    """验证设备接口保留离线和未授权状态，不将其标记为可用。"""
    # Step 1：使用纯模拟 ADB 返回三类设备状态。
    adb = Mock(spec=ADBClient)
    adb.list_devices.return_value = [("tv-online", "device"), ("tv-offline", "offline"), ("tv-locked", "unauthorized")]
    client.app.dependency_overrides[get_device_service] = lambda: DeviceService(lambda: adb)

    # Step 2：读取接口并确认原始状态完整保留。
    response = client.get("/api/v1/devices")
    assert response.status_code == 200
    assert response.json()["devices"] == [
        {"serial": "tv-online", "state": "device"},
        {"serial": "tv-offline", "state": "offline"},
        {"serial": "tv-locked", "state": "unauthorized"},
    ]
    adb.list_devices.assert_called_once_with()


def test_adb_failure_is_unavailable_without_raw_details(client: TestClient) -> None:
    """验证 ADB 查询失败返回不可用，且不回显底层错误内容。"""
    # Step 1：模拟包含内部诊断内容的 ADB 错误。
    adb = Mock(spec=ADBClient)
    adb.list_devices.side_effect = ADBError("internal-diagnostic-detail")
    client.app.dependency_overrides[get_device_service] = lambda: DeviceService(lambda: adb)

    # Step 2：确认接口不把查询失败伪装为空设备列表或泄漏内部信息。
    response = client.get("/api/v1/devices")
    assert response.status_code == 503
    assert "internal-diagnostic-detail" not in response.text
    assert "设备查询不可用" in response.json()["detail"]


def test_no_devices_returns_empty_inventory(client: TestClient) -> None:
    """验证没有设备时返回空清单，不伪造在线设备。"""
    # Step 1：模拟成功读取到空设备清单。
    adb = Mock(spec=ADBClient)
    adb.list_devices.return_value = []
    client.app.dependency_overrides[get_device_service] = lambda: DeviceService(lambda: adb)

    # Step 2：确认查询成功且设备清单为空。
    response = client.get("/api/v1/devices")
    assert response.status_code == 200
    assert response.json() == {"devices": [], "source": "adb"}


def test_catalog_reads_functions_without_importing_tests(client: TestClient, tmp_path: Path) -> None:
    """验证目录读取不会执行测试模块或启动任务。"""
    # Step 1：创建导入即失败的场景文件，用 AST 目录服务隔离执行。
    (tmp_path / "test_sample.py").write_text(
        'raise RuntimeError("不得导入")\ndef test_example():\n    """验证示例场景。"""\n    pass\n', encoding="utf-8",
    )
    client.app.dependency_overrides[get_task_service] = lambda: TaskService(tmp_path)

    # Step 2：确认场景被读取，且调度能力明确标记待实现。
    response = client.get("/api/v1/tasks/catalog")
    assert response.status_code == 200
    assert response.json()["scenarios"] == [{"id": "tests/test_sample.py::test_example", "title": "验证示例场景。"}]
    assert response.json()["scheduler"]["status"] == "not_implemented"


def test_invalid_catalog_returns_explicit_unavailable(client: TestClient, tmp_path: Path) -> None:
    """验证测试文件解析失败不会被当作空场景目录。"""
    # Step 1：准备含语法错误的测试文件。
    (tmp_path / "test_broken.py").write_text("def test_broken(\n", encoding="utf-8")
    client.app.dependency_overrides[get_task_service] = lambda: TaskService(tmp_path)

    # Step 2：确认接口明确报告不可用并隐藏本机路径。
    response = client.get("/api/v1/tasks/catalog")
    assert response.status_code == 503
    assert str(tmp_path) not in response.text


def test_reports_list_relative_metadata_and_download_as_attachment(client: TestClient, tmp_path: Path) -> None:
    """验证报告索引使用相对路径，下载以附件形式返回。"""
    # Step 1：创建带子目录的报告文件并注入报告服务。
    report_dir = tmp_path / "run-001"
    report_dir.mkdir()
    (report_dir / "result.html").write_text("<h1>report</h1>", encoding="utf-8")
    client.app.dependency_overrides[get_report_service] = lambda: ReportService(tmp_path)

    # Step 2：确认索引仅返回目录内的相对元数据。
    response = client.get("/api/v1/reports")
    assert response.status_code == 200
    assert response.json()["reports"][0]["path"] == "run-001/result.html"
    assert response.json()["reports"][0]["size_bytes"] == len(b"<h1>report</h1>")
    assert str(tmp_path) not in response.text

    # Step 3：下载报告并确认浏览器不会按 HTML 页面直接执行。
    download = client.get("/api/v1/reports/run-001/result.html")
    assert download.status_code == 200
    assert download.content == b"<h1>report</h1>"
    assert download.headers["content-type"] == "application/octet-stream"
    assert download.headers["content-disposition"].startswith("attachment;")


@pytest.mark.parametrize("path", ["../outside.txt", "/outside.txt", "C:/outside.txt", "sub/../../outside.txt", "..\\outside.txt", "report.txt:alternate", ""])
def test_report_service_rejects_unsafe_paths(tmp_path: Path, path: str) -> None:
    """验证报告下载拒绝父目录、绝对路径和 Windows 特殊路径。"""
    # Step 1：将报告访问限定于临时报告目录。
    service = ReportService(tmp_path / "reports")

    # Step 2：确认不可信路径统一被拒绝。
    with pytest.raises(ReportNotFoundError):
        service.resolve_report(path)


def test_encoded_traversal_is_not_downloadable(client: TestClient, tmp_path: Path) -> None:
    """验证 HTTP 编码路径不能下载报告目录外文件。"""
    # Step 1：在报告目录外创建文件，确保文件实际存在。
    (tmp_path / "outside.txt").write_text("outside", encoding="utf-8")
    (tmp_path / "reports").mkdir()
    client.app.dependency_overrides[get_report_service] = lambda: ReportService(tmp_path / "reports")

    # Step 2：尝试编码后的父目录跳转，并确认返回未找到。
    response = client.get("/api/v1/reports/%2E%2E%2Foutside.txt")
    assert response.status_code == 404
    assert "outside" not in response.text


def test_resolved_links_cannot_escape_report_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证解析后的文件链接不能逃出报告根目录。"""
    # Step 1：模拟一个报告路径解析到目录外文件，避免依赖 Windows 符号链接权限。
    root = tmp_path / "reports"
    root.mkdir()
    target = tmp_path / "outside.txt"
    target.write_text("outside", encoding="utf-8")
    link = root / "linked.txt"
    original_resolve = Path.resolve

    def resolve(path: Path, *args: object, **kwargs: object) -> Path:
        return target if path == link else original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)

    # Step 2：确认真实路径越界时拒绝提供文件。
    with pytest.raises(ReportNotFoundError):
        ReportService(root).resolve_report("linked.txt")


def test_report_limit_is_bounded_and_truncation_is_visible(client: TestClient, tmp_path: Path) -> None:
    """验证报告列表上限生效且明确标记截断。"""
    # Step 1：准备超过请求条数的报告。
    for index in range(3):
        (tmp_path / f"{index}.txt").write_text("report", encoding="utf-8")
    client.app.dependency_overrides[get_report_service] = lambda: ReportService(tmp_path)

    # Step 2：确认截断标记与参数上限。
    response = client.get("/api/v1/reports?limit=2")
    assert len(response.json()["reports"]) == 2
    assert response.json()["truncated"] is True
    assert client.get("/api/v1/reports?limit=201").status_code == 422


def test_settings_returns_status_only(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证设置接口只返回状态，不返回配置文件内容。"""
    # Step 1：提供存在的配置文件，并放入不应公开的配置字段。
    (tmp_path / "devices.yaml").write_text("private_detail: internal-config-detail\n", encoding="utf-8")
    monkeypatch.setattr(settings, "config_path", lambda name: tmp_path / name)

    # Step 2：确认响应只有明确公开的实施与存在状态。
    response = client.get("/api/v1/settings")
    assert response.json() == {
        "mode": "local-read-only",
        "device_config_present": True,
        "model_config_present": False,
        "ai_connection": "not_verified",
        "usb_ir": "not_configured",
    }
    assert "internal-config-detail" not in response.text


def test_defects_are_marked_not_implemented(client: TestClient) -> None:
    """验证缺陷能力未实现时明确报告状态。"""
    # Step 1：读取缺陷能力状态。
    response = client.get("/api/v1/defects")

    # Step 2：确认响应不会伪造已完成审核或分析。
    assert response.status_code == 200
    assert response.json()["status"] == "not_implemented"
