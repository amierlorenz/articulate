"""Tests for core.db and migrations."""
from pathlib import Path

from articulate.core.db import Database, discover_migrations


def test_empty_db_migrations(tmp_path: Path):
    db_path = tmp_path / "test.sqlite3"
    db = Database(db_path)
    applied = db.migrate()

    assert len(applied) > 0
    assert ("core", "001_core.sql") in applied

    # Check WAL mode and foreign keys
    with db.connect() as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode;")
        mode = cursor.fetchone()[0]
        # In temporary memory or file, journal_mode is wal
        assert mode.lower() == "wal"

        cursor.execute("PRAGMA foreign_keys;")
        assert cursor.fetchone()[0] == 1

        # Check tables exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row[0] for row in cursor.fetchall()}
        expected_tables = {
            "schema_migrations",
            "runs",
            "items",
            "item_events",
            "model_calls",
            "model_state",
            "claude_usage",
            "tasks",
            "vault_notes",
            "tombstones",
            "flags",
            "health",
            "handoff_files",
            "encore_snapshots",
            "courses",
            "units",
            "exams",
            "source_files",
            "pages",
            "concepts",
            "contrast_groups",
            "prompts",
            "guide_items",
        }
        for table in expected_tables:
            assert table in tables, f"Expected table {table} not found in DB"


def test_migrations_applied_only_once(tmp_path: Path):
    db_path = tmp_path / "test.sqlite3"
    db = Database(db_path)
    applied_first = db.migrate()
    assert len(applied_first) > 0

    applied_second = db.migrate()
    assert len(applied_second) == 0


def test_discover_slice_migrations(tmp_path: Path):
    # Create a simulated slice directory with migrations
    slices_dir = tmp_path / "src" / "articulate"
    dummy_migrations = slices_dir / "dummy" / "migrations"
    dummy_migrations.mkdir(parents=True)
    sql_file = dummy_migrations / "001_dummy.sql"
    sql_file.write_text("CREATE TABLE dummy_slice (id TEXT PRIMARY KEY);", encoding="utf-8")

    migrations = discover_migrations(root_dir=slices_dir)
    package_names = [m.package for m in migrations]
    assert "dummy" in package_names

    db_path = tmp_path / "test_slice.sqlite3"
    db = Database(db_path, search_dirs=[slices_dir])
    applied = db.migrate()

    applied_names = [name for pkg, name in applied if pkg == "dummy"]
    assert "001_dummy.sql" in applied_names

    with db.connect() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='dummy_slice';")
        assert cursor.fetchone() is not None

    # Re-running migration doesn't re-apply
    applied_again = db.migrate()
    assert len(applied_again) == 0
