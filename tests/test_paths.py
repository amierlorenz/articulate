"""Tests for core.paths."""
from articulate.core import paths


def test_paths_default():
    assert "articulate" in str(paths.get_data_dir())
    assert str(paths.get_db_path()).endswith("articulate.sqlite3")
    assert "courses.toml" in str(paths.courses_toml_path())
    assert "models.toml" in str(paths.models_toml_path())


def test_paths_env_override(monkeypatch, tmp_path):
    data_dir = tmp_path / "custom_data"
    human_dir = tmp_path / "custom_human"
    handoff_dir = tmp_path / "custom_handoff"

    monkeypatch.setenv("ARTICULATE_DATA", str(data_dir))
    monkeypatch.setenv("ARTICULATE_HUMAN", str(human_dir))
    monkeypatch.setenv("ARTICULATE_HANDOFF", str(handoff_dir))

    assert paths.get_data_dir() == data_dir
    assert paths.get_db_path() == data_dir / "articulate.sqlite3"
    assert paths.courses_toml_path() == human_dir / "courses.toml"
    assert paths.models_toml_path() == human_dir / "models.toml"
    assert paths.to_encore_dir() == handoff_dir / "to-encore"
    assert paths.from_encore_dir() == handoff_dir / "from-encore"


def test_calculated_paths(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("ARTICULATE_DATA", str(data_dir))

    rec_path = paths.transcript_path("rec-123", year="2026", month="11")
    assert rec_path == data_dir / "transcripts" / "2026" / "11" / "rec-123.json.gz"

    arch_path = paths.archive_path("mat305", "abcdef123456", "pdf")
    assert arch_path == data_dir / "archive" / "mat305" / "abcdef123456.pdf"

    pages_path = paths.archive_pages_path("mat305", "abcdef123456")
    assert pages_path == data_dir / "archive" / "mat305" / "abcdef123456.pages.json"

    work = paths.work_dir("run-01")
    assert work == data_dir / "work" / "run-01"

    ocr = paths.cache_ocr_path("pagehash123")
    assert ocr == data_dir / "cache" / "ocr" / "pagehash123.json"

    vl = paths.cache_vl_path("pagehash123")
    assert vl == data_dir / "cache" / "vl" / "pagehash123.json"
