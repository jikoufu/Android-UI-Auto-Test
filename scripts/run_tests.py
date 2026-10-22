"""从仓库根运行 Pytest，可选生成 JUnit 报告并限制总运行时间。"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def positive_seconds(value: str) -> float:
    """超时必须为有限正数。"""
    import math

    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a positive number") from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("timeout must be a finite positive number")
    return seconds


def main(argv: Sequence[str] | None = None) -> int:
    """转发 Pytest 参数和退出码，不使用 shell 执行命令。"""
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--timeout", type=positive_seconds, default=300.0, help="总超时秒数，默认 300")
    parser.add_argument("--report", action="store_true", help="生成 runtime/reports/pytest.xml")
    args, pytest_args = parser.parse_known_args(argv)
    if pytest_args[:1] == ["--"]:
        pytest_args = pytest_args[1:]

    command = [sys.executable, "-m", "pytest", *pytest_args]
    if args.report:
        from automation_core.config import RUNTIME_ROOT

        report = RUNTIME_ROOT / "reports" / "pytest.xml"
        report.parent.mkdir(parents=True, exist_ok=True)
        command.append(f"--junitxml={report}")

    try:
        completed = subprocess.run(command, cwd=REPO_ROOT, timeout=args.timeout, check=False)
    except subprocess.TimeoutExpired:
        print(f"Pytest exceeded the {args.timeout:g}-second timeout and was stopped.", file=sys.stderr)
        return 124
    except OSError:
        print("Unable to start Pytest with the current Python interpreter.", file=sys.stderr)
        return 1
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
