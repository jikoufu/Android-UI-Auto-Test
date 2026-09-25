"""封装带设备选择和超时控制的 ADB 命令。"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Sequence

from models.device_state import DeviceState


class ADBError(RuntimeError):
    """ADB 命令无法完成时抛出的异常。"""


class ADBClient:
    def __init__(
        self,
        serial: str | None = None,
        adb_path: str = "adb",
        command_timeout: float = 15,
        reports_dir: str | Path = "reports",
    ) -> None:
        """保存设备 serial、ADB 程序路径、超时和报告目录。"""
        self.serial = serial or None
        self.adb_path = adb_path
        self.command_timeout = command_timeout
        self.reports_dir = Path(reports_dir)

    def _command(self, args: Sequence[str]) -> list[str]:
        """组装包含可选 serial 的 ADB 命令参数。"""
        command = [self.adb_path]
        if self.serial:
            command.extend(["-s", self.serial])
        command.extend(str(arg) for arg in args)
        return command

    def run(self, *args: str, timeout: float | None = None) -> str:
        """运行 ADB 命令；命令失败或超时时抛出 ADBError。"""
        command = self._command(args)
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout or self.command_timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ADBError(f"ADB executable not found: {self.adb_path}") from exc
        except subprocess.TimeoutExpired as exc:
            raise ADBError(f"ADB command timed out after {exc.timeout} seconds") from exc
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown ADB error"
            raise ADBError(detail[:500])
        return result.stdout.strip()

    def run_shell(self, command: str, timeout: float | None = None) -> str:
        """在当前选定的 Android 设备上运行 Shell 命令。"""
        return self.run("shell", command, timeout=timeout)

    def list_devices(self) -> list[tuple[str, str]]:
        """读取 ADB 设备列表，返回 serial 与连接状态。"""
        output = self.run("devices", "-l")
        devices: list[tuple[str, str]] = []
        for line in output.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                devices.append((parts[0], parts[1]))
        return devices

    def _selected_serial(self) -> str:
        """沿用显式 serial，或在仅有一台在线设备时自动选择。"""
        if self.serial:
            return self.serial
        online = [serial for serial, state in self.list_devices() if state == "device"]
        if not online:
            raise ADBError("No Android device is connected; set ANDROID_SERIAL or connect one device")
        if len(online) > 1:
            raise ADBError("More than one Android device is connected; set ANDROID_SERIAL")
        self.serial = online[0]
        return self.serial

    @property
    def device_serial(self) -> str:
        """返回当前选定的设备 serial；未指定时沿用 ADB 的设备选择规则。"""
        return self._selected_serial()

    def current_activity(self) -> str | None:
        """读取当前前台 Activity；无法解析时返回 None。"""
        output = self.run_shell("dumpsys activity activities")
        patterns = (
            r"mResumedActivity:.*?\s([\w.$]+/[^\s}]+)",
            r"topResumedActivity=ActivityRecord\{[^ ]+\s([^\s}]+)",
        )
        for pattern in patterns:
            match = re.search(pattern, output)
            if match:
                return match.group(1)
        return None

    def dump_ui(self) -> Path:
        """通过 ADB 命令保存 UIAutomator XML，供诊断或离线分析。"""
        remote_path = "/sdcard/window.xml"
        self.run_shell(f"uiautomator dump {remote_path}", timeout=max(self.command_timeout, 30))
        xml = self.run_shell(f"cat {remote_path}", timeout=max(self.command_timeout, 30))
        output_dir = self.reports_dir / "ui_dump"
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "window.xml"
        path.write_text(xml, encoding="utf-8")
        return path

    def take_screenshot(self) -> Path:
        """通过 ADB screencap 保存截图，返回图片路径。"""
        command = self._command(["exec-out", "screencap", "-p"])
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                timeout=self.command_timeout,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise ADBError("Could not capture Android screenshot") from exc
        if result.returncode:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise ADBError(detail[:500] or "ADB screenshot failed")
        output_dir = self.reports_dir / "screenshots"
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "latest.png"
        path.write_bytes(result.stdout)
        return path

    def get_device_state(self) -> DeviceState:
        """收集设备型号、厂商、系统版本和当前 Activity。"""
        serial = self._selected_serial()

        def getprop(name: str) -> str | None:
            """读取系统属性；空值转换为 None。"""
            value = self.run_shell(f"getprop {name}")
            return value or None

        return DeviceState(
            serial=serial,
            model=getprop("ro.product.model"),
            manufacturer=getprop("ro.product.manufacturer"),
            android_version=getprop("ro.build.version.release"),
            current_activity=self.current_activity(),
        )
