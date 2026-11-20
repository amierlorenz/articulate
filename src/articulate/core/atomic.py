"""Atomic file writes with temporary files and rename."""
import os
from pathlib import Path


def write_atomic(path: Path | str, content: bytes | str) -> None:
    """
    Write content to a file atomically (§3.13):
    write .<name>.tmp in the same folder, fsync, rename.
    Parent directories are created if missing.
    """
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = target.parent / f".{target.name}.tmp"
    data = content.encode("utf-8") if isinstance(content, str) else content

    with open(tmp_path, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())

    os.replace(tmp_path, target)
