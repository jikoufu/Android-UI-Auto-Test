"""Publish and verify write-once snapshots for explicit stage approvals."""

from hashlib import sha256
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

if __package__:
    from .requirements_document import RequirementsError, artifact_path, checked_artifact
else:
    from requirements_document import RequirementsError, artifact_path, checked_artifact


def snapshot_path(worktree: Path, task_id: str, stage: str, approval_id: str) -> Path:
    directory = artifact_path(worktree, task_id).parent / "approved"
    # Keep the path short for Windows checkouts. The state record holds the digest.
    return directory / f"{stage[0]}-{approval_id}.md"


def publish_snapshot(worktree: Path, path: Path, data: bytes) -> None:
    """Publish complete bytes atomically without replacing any existing snapshot."""
    checked_artifact(worktree, path, prepare=True)
    stream = NamedTemporaryFile(mode="wb", dir=path.parent, delete=False)
    temporary = Path(stream.name)
    try:
        with stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "nt":
            temporary.rename(path)  # Windows rename fails if the destination exists.
        else:
            os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_snapshot(worktree: Path, path: Path, digest: str) -> bytes:
    data = checked_artifact(worktree, path).read_bytes()
    if sha256(data).hexdigest() != digest:
        raise RequirementsError(f"Approved snapshot SHA-256 mismatch: {path}")
    return data
