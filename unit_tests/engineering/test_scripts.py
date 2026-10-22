"""工程入口与源码打包验证，全部隔离真实设备、Web 服务和外部 AI。"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock
from zipfile import ZipFile

import pytest

from automation_core import config
from scripts import package_release, run_tests, start_backend


def test_backend_config_and_command_line_override(monkeypatch, tmp_path):
    """验证后端读取统一配置，并允许命令行覆盖监听参数。"""
    # Step 1：替换服务启动函数与配置读取，从其他目录调用入口。
    uvicorn_run = Mock()
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=uvicorn_run))
    configuration = Mock(return_value={"backend": {"host": "127.0.0.2", "port": 8123}})
    monkeypatch.setattr(config, "load_yaml", configuration)
    monkeypatch.chdir(tmp_path)

    # Step 2：确认默认读取统一配置并指定仓库导入目录，不依赖当前目录。
    assert start_backend.main([]) == 0
    configuration.assert_called_once_with(config.config_path("environments.yaml"))
    uvicorn_run.assert_called_once_with(
        "backend.main:app", host="127.0.0.2", port=8123, reload=False,
        app_dir=str(start_backend.REPO_ROOT), reload_dirs=None,
    )

    # Step 3：确认显式参数覆盖配置，并将热重载限定在源码目录。
    assert start_backend.main(["--host", "localhost", "--port", "9001", "--reload"]) == 0
    assert uvicorn_run.call_args.kwargs == {
        "host": "localhost", "port": 9001, "reload": True,
        "app_dir": str(start_backend.REPO_ROOT),
        "reload_dirs": [str(start_backend.REPO_ROOT / "backend"), str(start_backend.REPO_ROOT / "automation_core")],
    }


def test_backend_missing_config_uses_loopback_defaults(monkeypatch):
    """验证缺省配置仅在本机回环地址启动。"""
    # Step 1：模拟空配置和服务入口，避免启动真实服务。
    uvicorn_run = Mock()
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=uvicorn_run))
    monkeypatch.setattr(config, "load_yaml", lambda _: {})

    # Step 2：确认默认地址和端口符合本机部署约定。
    assert start_backend.main([]) == 0
    assert uvicorn_run.call_args.kwargs["host"] == "127.0.0.1"
    assert uvicorn_run.call_args.kwargs["port"] == 8000


@pytest.mark.parametrize("port", [0, 65536, True, 8000.5, "invalid"])
def test_backend_rejects_invalid_config_without_starting(monkeypatch, port):
    """验证非法配置端口不会启动服务。"""
    # Step 1：提供非法端口并替换真实服务启动函数。
    uvicorn_run = Mock()
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=uvicorn_run))
    monkeypatch.setattr(config, "load_yaml", lambda _: {"backend": {"port": port}})

    # Step 2：确认参数错误明确退出，且没有尝试监听端口。
    with pytest.raises(SystemExit) as error:
        start_backend.main([])
    assert error.value.code == 2
    uvicorn_run.assert_not_called()


def test_test_runner_forwards_arguments_exit_code_and_repository_cwd(monkeypatch, tmp_path):
    """验证测试入口原样转发参数，固定仓库目录并保留 Pytest 退出码。"""
    # Step 1：从其他目录模拟一次失败的 Pytest 子进程。
    execute = Mock(return_value=SimpleNamespace(returncode=5))
    monkeypatch.setattr(run_tests.subprocess, "run", execute)
    monkeypatch.chdir(tmp_path)

    # Step 2：确认使用当前解释器和参数数组，设备标记表达式不经 shell 解析。
    result = run_tests.main(["--timeout", "25", "--", "-q", "-m", "device and not ai"])
    assert result == 5
    execute.assert_called_once_with(
        [sys.executable, "-m", "pytest", "-q", "-m", "device and not ai"],
        cwd=run_tests.REPO_ROOT, timeout=25.0, check=False,
    )


def test_test_runner_places_junit_under_configured_runtime(monkeypatch, tmp_path):
    """验证报告写入统一运行目录，默认总超时保持有限。"""
    # Step 1：隔离运行目录并模拟成功执行 Pytest。
    execute = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(run_tests.subprocess, "run", execute)
    monkeypatch.setattr(config, "RUNTIME_ROOT", tmp_path / "runtime")

    # Step 2：确认只添加指定 JUnit 路径，且报告目录已经准备好。
    assert run_tests.main(["--report", "-q", "unit_tests"]) == 0
    report = tmp_path / "runtime" / "reports" / "pytest.xml"
    assert report.parent.is_dir()
    assert execute.call_args.args[0] == [sys.executable, "-m", "pytest", "-q", "unit_tests", f"--junitxml={report}"]
    assert execute.call_args.kwargs["timeout"] == 300.0


def test_test_runner_reports_timeout_without_exposing_command(monkeypatch, capsys):
    """验证测试超时返回明确失败状态，不把子进程参数写入错误信息。"""
    # Step 1：模拟子进程超过整体时间限制。
    execute = Mock(side_effect=subprocess.TimeoutExpired(["private-argument"], 2))
    monkeypatch.setattr(run_tests.subprocess, "run", execute)

    # Step 2：确认返回约定超时码，错误信息不包含完整命令。
    assert run_tests.main(["--timeout", "2", "-q"]) == 124
    error = capsys.readouterr().err
    assert "2-second timeout" in error
    assert "private-argument" not in error


@pytest.mark.parametrize("timeout", ["0", "-1", "inf", "nan"])
def test_test_runner_rejects_unbounded_timeout(monkeypatch, timeout):
    """验证超时参数不能禁用有限运行上限。"""
    # Step 1：替换子进程执行，准备非法超时输入。
    execute = Mock()
    monkeypatch.setattr(run_tests.subprocess, "run", execute)

    # Step 2：确认非法超时被拒绝且没有启动测试。
    with pytest.raises(SystemExit) as error:
        run_tests.main(["--timeout", timeout])
    assert error.value.code == 2
    execute.assert_not_called()


def test_release_contains_source_templates_and_excludes_private_artifacts(tmp_path):
    """验证源码包包含各模块和模板，同时排除真实任务、私有配置和运行产物。"""
    # Step 1：建立最小源码树和不允许进入部署包的样例文件。
    allowed = {
        "README.md", ".env.example", "pyproject.toml", "automation_core/device/client.py",
        "workflows/network.py", "backend/main.py", "frontend/index.html", "frontend/js/app.js",
        "agent_harness/policies/project.md", "agent_harness/workflows/test_delivery.yaml",
        "configs/devices.yaml", "configs/models.yaml", "configs/environments.yaml",
        "configs/accounts.example.yaml", "scripts/run_tests.py", "tasks/README.md",
        "tasks/_template/task.yaml", "data/README.md", "unit_tests/test_example.py",
    }
    excluded = {
        ".env", ".env.local", ".git/config", ".venv/site.py", ".idea/workspace.xml",
        "runtime/reports/output.md", "reports/screenshot.png", "dist/old.zip",
        "configs/accounts.yaml", "configs/production.yaml", "data/customers.csv",
        "data/private.py", "tasks/real-job/requirement.md", "tasks/real-job/task.yaml",
        "automation_core/__pycache__/client.pyc", "automation_core/private/account.py",
        "frontend/node_modules/pkg/index.js", "frontend/.env.example",
        "agent_harness/credentials.yaml", "agent_harness/workflows/private.yaml",
        "agent_harness/runtime/task.md", "scripts/private.py",
    }
    for name in allowed | excluded:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic fixture", encoding="utf-8")

    # Step 2：检查 ZIP 清单，确认没有夹带未列入白名单的文件。
    output = package_release.create_release(tmp_path, tmp_path / "dist" / "release.zip")
    with ZipFile(output) as archive:
        assert set(archive.namelist()) == allowed

    # Step 3：确认同名打包不会覆盖已有交付物。
    before = output.read_bytes()
    with pytest.raises(FileExistsError):
        package_release.create_release(tmp_path, output)
    assert output.read_bytes() == before


def test_release_rejects_resolved_paths_outside_repository(monkeypatch, tmp_path):
    """验证即使文件名属于白名单，解析后越界的文件也不会打包。"""
    # Step 1：模拟受支持源码文件解析到仓库外，无需创建系统符号链接。
    source = tmp_path / "backend" / "main.py"
    source.parent.mkdir()
    source.write_text("synthetic fixture", encoding="utf-8")
    resolve = Path.resolve
    outside = tmp_path.parent / "outside.py"
    monkeypatch.setattr(Path, "resolve", lambda path, *args, **kwargs: outside if path == source else resolve(path, *args, **kwargs))

    # Step 2：确认路径边界检查排除了越界文件。
    assert list(package_release.release_files(tmp_path)) == []


def test_release_skips_linked_directories_and_files(monkeypatch, tmp_path):
    """验证源目录或文件被标记为链接时均被排除。"""
    # Step 1：建立普通文件并模拟链接标志，避免依赖 Windows 链接权限。
    linked_directory = tmp_path / "backend" / "linked"
    linked_directory.mkdir(parents=True)
    (linked_directory / "secret.py").write_text("synthetic fixture", encoding="utf-8")
    linked_file = tmp_path / "README.md"
    linked_file.write_text("synthetic fixture", encoding="utf-8")
    monkeypatch.setattr(package_release, "is_link", lambda path: path in {linked_directory, linked_file})

    # Step 2：确认扫描不会读取链接目录下的文件或链接文件。
    assert list(package_release.release_files(tmp_path)) == []


@pytest.mark.parametrize("name", ["../release.zip", "..\\release.zip", "C:release.zip", "release.exe"])
def test_release_cli_rejects_output_path_escape(monkeypatch, tmp_path, name):
    """验证打包输出只能使用 dist 目录下的普通 ZIP 文件名。"""
    # Step 1：替换仓库路径，准备带越界路径或错误格式的输出名称。
    monkeypatch.setattr(package_release, "REPO_ROOT", tmp_path)

    # Step 2：确认拒绝非法名称，并且没有创建输出目录。
    with pytest.raises(SystemExit) as error:
        package_release.main(["--name", name])
    assert error.value.code == 2
    assert not (tmp_path / "dist").exists()


@pytest.mark.parametrize("script", ["start_backend.py", "run_tests.py", "package_release.py"])
def test_script_help_works_from_another_directory(tmp_path, script):
    """验证三个工程入口能从任意工作目录直接执行帮助命令。"""
    # Step 1：使用当前解释器从临时目录执行脚本帮助，不启动服务或设备。
    result = subprocess.run(
        [sys.executable, str(run_tests.REPO_ROOT / "scripts" / script), "--help"],
        cwd=tmp_path, capture_output=True, text=True, timeout=10, check=False,
    )

    # Step 2：确认帮助命令成功且展示对应脚本入口。
    assert result.returncode == 0, result.stderr
    assert script in result.stdout
