"""Tests for core.config."""
from pathlib import Path

import pytest

from articulate.core.config import (
    ArticulateConfig,
    ConfigError,
    CoursesConfig,
    ModelsConfig,
    jobs_needing_file,
    load_articulate_config,
    load_courses_config,
    load_models_config,
)
from articulate.core.db import Database

FIXTURES = Path(__file__).parent / "fixtures" / "core"


def test_load_courses_valid():
    cfg = load_courses_config(FIXTURES / "courses_valid.toml")
    assert isinstance(cfg, CoursesConfig)
    assert "mat305" in cfg.courses
    course = cfg.courses["mat305"]
    assert course.canvas_id == 12345
    assert course.name == "MAT-305 Prob & Stats"
    assert len(course.exams) == 1
    assert course.exams[0].key == "final"


def test_load_courses_unknown_keys_ignored():
    cfg = load_courses_config(FIXTURES / "courses_unknown_keys.toml")
    assert "mat305" in cfg.courses
    assert cfg.courses["mat305"].canvas_id == 12345


def test_load_courses_invalid_names_file_and_key():
    with pytest.raises(ConfigError) as exc_info:
        load_courses_config(FIXTURES / "courses_invalid.toml")

    err = exc_info.value
    assert "courses_invalid.toml" in str(err)
    assert err.file_name == "courses_invalid.toml"
    assert err.key is not None
    assert "canvas_id" in err.key


def test_load_models_valid():
    cfg = load_models_config(FIXTURES / "models_valid.toml")
    assert isinstance(cfg, ModelsConfig)
    assert "gemini-3.8-flash" in cfg.gemini.models
    assert cfg.gemini.models["gemini-3.8-flash"].rpm == 5
    assert cfg.claude.model == "sonnet"
    assert cfg.chains["extract"] == ["gemini-3.8-flash", "claude@ramp"]


def test_load_models_invalid_names_file_and_key():
    with pytest.raises(ConfigError) as exc_info:
        load_models_config(FIXTURES / "models_invalid.toml")

    err = exc_info.value
    assert "models_invalid.toml" in str(err)
    assert err.file_name == "models_invalid.toml"
    assert err.key is not None
    assert "rpm" in err.key


def test_load_articulate_valid():
    cfg = load_articulate_config(FIXTURES / "articulate_valid.toml")
    assert isinstance(cfg, ArticulateConfig)
    assert cfg.vault.name == "MyVault"
    assert cfg.encore.bundle_id == "com.amierlorenz.Encore"


def test_load_articulate_invalid_names_file_and_key():
    with pytest.raises(ConfigError) as exc_info:
        load_articulate_config(FIXTURES / "articulate_invalid.toml")

    err = exc_info.value
    assert "articulate_invalid.toml" in str(err)
    assert err.file_name == "articulate_invalid.toml"
    assert err.key is not None
    assert "vault" in err.key or "path" in err.key


def test_missing_file_names_file():
    with pytest.raises(ConfigError) as exc_info:
        load_courses_config(Path("/nonexistent/courses.toml"))

    err = exc_info.value
    assert "courses.toml" in str(err)
    assert err.file_name == "courses.toml"


def test_config_raises_health_row_on_db(tmp_path: Path):
    db_path = tmp_path / "health_test.sqlite3"
    db = Database(db_path)
    db.migrate()

    with pytest.raises(ConfigError):
        load_courses_config(FIXTURES / "courses_invalid.toml", db=db)

    with db.connect() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT kind, key, detail FROM health WHERE kind = 'invalid_config';")
        row = cursor.fetchone()
        assert row is not None
        assert row["key"] == "courses_invalid.toml"
        assert "canvas_id" in row["detail"]


def test_jobs_needing_file():
    assert "nightly" in jobs_needing_file("courses.toml")
    assert "watch" not in jobs_needing_file("courses.toml")

    assert "watch" in jobs_needing_file("models.toml")
    assert "nightly" in jobs_needing_file("models.toml")

    assert "watch" in jobs_needing_file("articulate.toml")
    assert "nightly" in jobs_needing_file("articulate.toml")
