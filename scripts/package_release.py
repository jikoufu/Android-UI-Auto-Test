"""生成本地源码部署 ZIP，包含前端、Python、Harness 和文档；不是独立 EXE。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import stat
from typing import Iterator, Sequence
from zipfile import ZIP_DEFLATED, ZipFile


REPO_ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {
    "README.md", "AGENTS.md", "CLAUDE.md", "pyproject.toml", "requirements.txt",
    ".env.example", ".gitignore",
}
FIXED_FILES = {
    "configs/accounts.example.yaml", "configs/devices.yaml", "configs/environments.yaml",
    "configs/models.yaml", "configs/README.md", "data/README.md", "data/.gitkeep", "tasks/README.md",
    "data/baselines/README.md", "data/dictionaries/README.md", "data/templates/README.md",
    "tasks/_template/task.yaml", "tasks/_template/requirement.md",
    "tasks/_template/design.md", "tasks/_template/verification.json",
    "scripts/start_backend.py", "scripts/run_tests.py", "scripts/package_release.py",
    ".agents/skills/pytest-test-writer/SKILL.md",
    "agent_harness/workflows/feature_development.yaml",
    "agent_harness/workflows/test_development.yaml",
    "agent_harness/workflows/defect_review.yaml",
    "agent_harness/workflows/test_delivery.yaml",
}
SOURCE_DIRECTORIES = {
    "automation_core": {".py", ".md"},
    "workflows": {".py", ".md"},
    "backend": {".py", ".md"},
    "frontend": {".html", ".css", ".js", ".md", ".svg"},
    "agent_harness": {".py", ".md"},
    "tests": {".py", ".md"},
    "unit_tests": {".py", ".md"},
    "docs": {".md"},
}
EXCLUDED_PARTS = {
    ".git", ".venv", "venv", ".idea", ".vscode", ".pytest_cache", "__pycache__",
    "node_modules", "runtime", "reports", "dist", "build", "private", "secrets",
}


def is_link(path: Path) -> bool:
    """同时拒绝符号链接和 Windows junction 等重解析点。"""
    return path.is_symlink() or bool(
        getattr(path.lstat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def safe_file(root: Path, path: Path) -> bool:
    """只接受仓库内的普通文件，且路径的任何一级都不是链接。"""
    if not path.is_file() or not path.resolve().is_relative_to(root):
        return False
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        if is_link(current):
            return False
    return True


def release_files(root: Path) -> Iterator[Path]:
    """固定白名单不包含实际任务、测试数据、账户文件或运行证据。"""
    root = root.resolve()
    selected: set[Path] = set()
    for relative in ROOT_FILES | FIXED_FILES:
        path = root / relative
        if safe_file(root, path):
            selected.add(path)

    for directory, extensions in SOURCE_DIRECTORIES.items():
        source = root / directory
        if not source.is_dir() or is_link(source):
            continue
        for parent, directories, filenames in os.walk(source, followlinks=False):
            parent_path = Path(parent)
            directories[:] = [
                name for name in directories
                if name.casefold() not in EXCLUDED_PARTS
                and not name.startswith(".")
                and not name.endswith(".egg-info")
                and not is_link(parent_path / name)
            ]
            for name in filenames:
                path = parent_path / name
                if (
                    path.suffix.lower() in extensions
                    and not name.startswith(".")
                    and safe_file(root, path)
                ):
                    selected.add(path)
    yield from sorted(selected, key=lambda path: path.relative_to(root).as_posix())


def create_release(root: Path, output: Path) -> Path:
    """以排他创建方式写入 ZIP，已有包不会被覆盖。"""
    root = root.resolve()
    files = list(release_files(root))
    if not files:
        raise ValueError("No release source files were found")
    output.parent.mkdir(parents=True, exist_ok=True)
    # 先独占创建文件，避免同名输出或符号链接导致覆盖已有内容。
    with output.open("xb") as stream:
        try:
            with ZipFile(stream, mode="w", compression=ZIP_DEFLATED) as archive:
                for path in files:
                    if not safe_file(root, path):
                        raise ValueError("Release input changed while packaging")
                    archive.write(path, arcname=path.relative_to(root).as_posix())
        except Exception:
            stream.close()
            output.unlink(missing_ok=True)
            raise
    return output


def main(argv: Sequence[str] | None = None) -> int:
    """输出只写到仓库 dist 下，不执行任何发布或上传。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", help="dist 下的 ZIP 文件名；同名文件不会覆盖")
    args = parser.parse_args(argv)
    name = args.name or f"tv-automation-source-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.zip"
    if (
        Path(name).name != name or "/" in name or "\\" in name or ":" in name
        or not name.lower().endswith(".zip") or name.startswith(".")
    ):
        parser.error("--name must be a plain ZIP file name")
    destination = REPO_ROOT / "dist"
    if destination.exists() and is_link(destination):
        parser.error("dist must be a regular directory inside the repository")
    if not destination.resolve().is_relative_to(REPO_ROOT.resolve()):
        parser.error("dist must stay inside the repository")
    output = destination / name
    try:
        create_release(REPO_ROOT, output)
    except FileExistsError:
        parser.error("Release already exists; choose another --name")
    except (OSError, ValueError):
        parser.error("Unable to create the source release ZIP")
    print(f"Source deployment ZIP (not a standalone executable): {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
