"""读取 pytest 场景目录，不导入或执行设备测试。"""

import ast
from pathlib import Path

from backend.schemas import CapabilityStatus, TaskCatalog, TaskSummary


class CatalogUnavailableError(RuntimeError):
    """测试目录无法安全读取或解析。"""


class TaskService:
    def __init__(self, tests_dir: Path) -> None:
        self._tests_dir = tests_dir

    def catalog(self) -> TaskCatalog:
        scenarios: list[TaskSummary] = []
        root = self._tests_dir.resolve()
        try:
            for path in sorted(root.rglob("test_*.py")):
                if not path.resolve().is_relative_to(root):
                    continue
                module = ast.parse(path.read_text(encoding="utf-8-sig"))
                for node in module.body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                        title = ast.get_docstring(node) or node.name
                        scenarios.append(TaskSummary(
                            id=f"tests/{path.relative_to(root).as_posix()}::{node.name}",
                            title=title.splitlines()[0],
                        ))
        except (OSError, UnicodeError, SyntaxError) as exc:
            raise CatalogUnavailableError("无法读取测试场景目录，请检查测试文件。") from exc
        return TaskCatalog(
            scenarios=scenarios,
            scheduler=CapabilityStatus(status="not_implemented", detail="当前通过 pytest 命令行执行；Web 任务调度尚未实现。"),
        )
