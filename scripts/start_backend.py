"""启动本机 Web 服务；不主动连接设备或启动测试。"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def port_number(value: str | int) -> int:
    """拒绝无效监听端口。"""
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise argparse.ArgumentTypeError("port must be an integer")
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def main(argv: Sequence[str] | None = None) -> int:
    """加载统一配置并将显式参数传给 uvicorn。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", help="覆盖 configs/environments.yaml 的 backend.host")
    parser.add_argument("--port", type=port_number, help="覆盖 backend.port")
    parser.add_argument("--reload", action="store_true", help="开发时监视源码修改")
    args = parser.parse_args(argv)

    from automation_core.config import config_path, load_yaml
    from yaml import YAMLError

    try:
        settings = load_yaml(config_path("environments.yaml")).get("backend", {})
        if not isinstance(settings, dict):
            raise ValueError("backend configuration must be a mapping")
        host = args.host if args.host is not None else settings.get("host", "127.0.0.1")
        if not isinstance(host, str) or not host.strip():
            raise ValueError("backend.host must be a non-empty string")
        port = args.port if args.port is not None else port_number(settings.get("port", 8000))
    except (ValueError, argparse.ArgumentTypeError, YAMLError):
        parser.error("Invalid backend settings in configs/environments.yaml")

    try:
        import uvicorn
    except ImportError:
        parser.error('Web dependencies are missing; run: python -m pip install -e ".[web]"')

    uvicorn.run(
        "backend.main:app",
        host=host,
        port=port,
        reload=args.reload,
        app_dir=str(REPO_ROOT),
        reload_dirs=[str(REPO_ROOT / "backend"), str(REPO_ROOT / "automation_core")]
        if args.reload
        else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
