"""Tests for core.atomic."""
from pathlib import Path

from articulate.core.atomic import write_atomic


def test_write_atomic_text(tmp_path: Path):
    target = tmp_path / "subdir" / "target.txt"
    content = "Hello, Articulate!"
    write_atomic(target, content)

    assert target.exists()
    assert target.read_text(encoding="utf-8") == content

    # Verify no leftover .tmp files
    tmp_files = list(target.parent.glob(".*.tmp"))
    assert len(tmp_files) == 0


def test_write_atomic_bytes(tmp_path: Path):
    target = tmp_path / "binary.dat"
    content = b"\x00\x01\x02\x03\xff"
    write_atomic(target, content)

    assert target.exists()
    assert target.read_bytes() == content


def test_write_atomic_overwrite(tmp_path: Path):
    target = tmp_path / "overwrite.txt"
    write_atomic(target, "first")
    assert target.read_text(encoding="utf-8") == "first"

    write_atomic(target, "second")
    assert target.read_text(encoding="utf-8") == "second"
