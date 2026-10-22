"""保护单仓库模块边界，防止核心依赖 Web 或研发工作流。"""

import ast
from pathlib import Path
import subprocess
import sys

from automation_core.config import PROJECT_ROOT


def _imports(path: Path) -> list[str]:
    modules = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def test_production_modules_follow_dependency_direction():
    """验证核心、业务流程、后端和 Harness 不产生反向依赖。"""
    # Step 1：声明各层禁止依赖的模块，扫描真实源文件。
    boundaries = {
        "automation_core": {"backend", "frontend", "workflows", "tests", "unit_tests", "pytest", "fastapi", "agent_harness"},
        "workflows": {"backend", "frontend", "tests", "pytest", "agent_harness", "automation_core.ai"},
        "backend": {"tests", "unit_tests", "pytest", "agent_harness"},
        "agent_harness": {"automation_core", "backend", "frontend", "workflows", "tests"},
        "automation_core/device": {"automation_core.ai", "automation_core.evidence", "automation_core.bootstrap"},
        "automation_core/remote": {"automation_core.ai", "automation_core.bootstrap"},
        "automation_core/navigation": {"automation_core.ai", "automation_core.bootstrap"},
    }
    violations = []
    for directory, forbidden in boundaries.items():
        for source in (PROJECT_ROOT / directory).rglob("*.py"):
            for module in _imports(source):
                if any(module == prefix or module.startswith(prefix + ".") for prefix in forbidden):
                    violations.append(f"{source.relative_to(PROJECT_ROOT)} -> {module}")

    # Step 2：发现反向导入时明确报告文件和模块。
    assert not violations, "\n".join(violations)


def test_uiautomator_is_confined_to_one_adapter():
    """验证 uiautomator2 只由唯一设备适配层直接导入。"""
    # Step 1：扫描核心、业务、后端和设备场景的直接导入。
    consumers = set()
    for directory in ("automation_core", "workflows", "backend", "tests"):
        for source in (PROJECT_ROOT / directory).rglob("*.py"):
            if any(module == "uiautomator2" or module.startswith("uiautomator2.") for module in _imports(source)):
                consumers.add(source.relative_to(PROJECT_ROOT).as_posix())

    # Step 2：只有 UIA2 适配器可以依赖第三方驱动。
    assert consumers == {"automation_core/device/uia2_client.py"}


def test_core_bootstraps_without_web_pytest_or_harness(tmp_path):
    """验证独立进程可从任意工作目录装配核心且不访问设备或 Web。"""
    # Step 1：在隔离进程阻止外部设备操作，并从项目源码导入核心。
    script = f'''
import sys, subprocess
sys.path.insert(0, {str(PROJECT_ROOT)!r})
def forbidden(*args, **kwargs):
    raise AssertionError("device process started during construction")
subprocess.run = forbidden
from automation_core.bootstrap import create_tv
tv = create_tv({{"serial": "offline", "command_timeout": 5}})
assert tv.ui.adb is tv.adb and tv.remote.adb is tv.adb
assert not ({{"backend", "fastapi", "pytest", "agent_harness", "workflows"}} & set(sys.modules))
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, capture_output=True, text=True, timeout=30)

    # Step 2：核心装配成功，无需启动任何上层入口。
    assert result.returncode == 0, result.stderr
