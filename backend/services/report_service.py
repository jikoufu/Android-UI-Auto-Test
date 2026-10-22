"""只索引和下载 runtime/reports 中的普通文件。"""

from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from backend.schemas import ReportList, ReportSummary


class ReportNotFoundError(FileNotFoundError):
    """报告不存在或路径不在报告目录内。"""


class ReportsUnavailableError(RuntimeError):
    """报告目录不可读。"""


class ReportService:
    def __init__(self, reports_dir: Path) -> None:
        self._reports_dir = reports_dir

    def resolve_report(self, report_path: str) -> Path:
        """拒绝绝对路径、父目录、Windows 特殊路径及目录外符号链接。"""
        relative = PurePosixPath(report_path)
        if not report_path or relative.is_absolute() or ".." in relative.parts or "\\" in report_path or ":" in report_path:
            raise ReportNotFoundError("报告不存在。")
        root = self._reports_dir.resolve()
        candidate = (root / relative).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            raise ReportNotFoundError("报告不存在。")
        return candidate

    def list_reports(self, limit: int = 100) -> ReportList:
        """返回相对路径和文件元数据，不暴露主机绝对路径。"""
        reports: list[ReportSummary] = []
        root = self._reports_dir.resolve()
        try:
            for path in sorted(root.rglob("*")):
                if not path.is_file():
                    continue
                relative = path.relative_to(root).as_posix()
                try:
                    safe_path = self.resolve_report(relative)
                except ReportNotFoundError:
                    continue
                if len(reports) >= limit:
                    return ReportList(reports=reports, truncated=True)
                stat = safe_path.stat()
                reports.append(ReportSummary(
                    path=relative,
                    size_bytes=stat.st_size,
                    modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                ))
        except OSError as exc:
            raise ReportsUnavailableError("无法读取报告目录。") from exc
        return ReportList(reports=reports)
