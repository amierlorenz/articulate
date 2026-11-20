"""Tests for Vault writer (Acceptance check 0.13)."""
from pathlib import Path

import pytest

from articulate.core.db import Database
from articulate.core.vault import NoteRef, Vault


@pytest.fixture
def vault_env(tmp_path: Path):
    db_path = tmp_path / "test.sqlite3"
    db = Database(db_path)
    db.migrate()
    vault_dir = tmp_path / "vault"
    vault = Vault(vault_dir, db)
    return vault, vault_dir, db


def test_create_note_create_only(vault_env):
    vault, _vault_dir, _ = vault_env
    ref = vault.create_note(
        "Literature Notes",
        "Process vs Thread",
        {"tags": ["cs"]},
        "A process has its own address space.",
        kind="literature",
        source_item_id="item-1",
    )
    assert ref is not None
    assert ref.path.exists()
    assert ref.path.name == "Process vs Thread.md"
    assert "agent: true" in ref.path.read_text()
    assert "articulate_id:" in ref.path.read_text()

    # Second note with same title gets " (2)"
    ref2 = vault.create_note(
        "Literature Notes",
        "Process vs Thread",
        {"tags": ["cs"]},
        "Another take on process and thread.",
        kind="literature",
        source_item_id="item-2",
    )
    assert ref2 is not None
    assert ref2.path.name == "Process vs Thread (2).md"


def test_create_note_tombstone_refused(vault_env):
    vault, _, db = vault_env
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO tombstones (kind, key, reason, created_at) VALUES ('articulate_id', 'tomb-1', 'deleted', '2026-11-20T00:00:00Z');"
        )
        conn.execute(
            "INSERT INTO tombstones (kind, key, reason, created_at) VALUES ('source_item_id', 'src-tomb-1', 'deleted', '2026-11-20T00:00:00Z');"
        )
        conn.commit()

    # Refused by articulate_id tombstone
    ref1 = vault.create_note(
        "Literature Notes",
        "Note 1",
        {"articulate_id": "tomb-1"},
        "Body",
        kind="literature",
        source_item_id="src-normal",
    )
    assert ref1 is None

    # Refused by source_item_id tombstone
    ref2 = vault.create_note(
        "Literature Notes",
        "Note 2",
        {},
        "Body",
        kind="literature",
        source_item_id="src-tomb-1",
    )
    assert ref2 is None


def test_regenerate_untouched_only(vault_env):
    vault, _, _ = vault_env
    ref = vault.create_note(
        "Literature Notes",
        "Regen Test",
        {},
        "Initial body.",
        kind="literature",
        source_item_id="item-3",
    )
    assert ref is not None
    art_id = ref.articulate_id

    # Regenerate while untouched succeeds
    ok = vault.regenerate(art_id, {"tags": ["updated"]}, "New regenerated body.")
    assert ok is True
    assert "New regenerated body." in ref.path.read_text()

    # Manually edit the file on disk
    ref.path.write_text("Amier edited this note manually.")

    # Regenerate refused once edited
    ok_after_edit = vault.regenerate(art_id, {}, "Attempt to overwrite.")
    assert ok_after_edit is False

    # Also test move: create new note, move it
    ref_move = vault.create_note(
        "Inbox",
        "Move Test",
        {},
        "Move me.",
        kind="inbox",
        source_item_id="item-4",
    )
    new_dest = ref_move.path.parent.parent / "Literature Notes" / ref_move.path.name
    ref_move.path.rename(new_dest)
    vault.scan()

    # Regenerate refused once moved
    ok_after_move = vault.regenerate(ref_move.articulate_id, {}, "Attempt after move.")
    assert ok_after_move is False


def test_scan_finds_moved_and_tombstones_missing(vault_env):
    vault, vault_dir, db = vault_env
    ref = vault.create_note(
        "Inbox",
        "Note to Move",
        {},
        "Content",
        kind="inbox",
        source_item_id="item-5",
    )
    art_id = ref.articulate_id

    # Move to Literature Notes
    dest = vault_dir / "Literature Notes" / "Note to Move.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    ref.path.rename(dest)

    # Before scan, DB has old path
    vault.scan()

    with db.connect() as conn:
        row = conn.execute("SELECT path, touched FROM vault_notes WHERE articulate_id = ?", (art_id,)).fetchone()
        assert row is not None
        assert "Literature Notes/Note to Move.md" in row["path"]
        assert row["touched"] == 1

    # Now delete the file
    dest.unlink()
    vault.scan()

    with db.connect() as conn:
        tomb = conn.execute("SELECT * FROM tombstones WHERE key = ?", (art_id,)).fetchone()
        assert tomb is not None
        assert tomb["kind"] == "articulate_id"


def test_write_system_skips_unchanged_hash(vault_env):
    vault, vault_dir, _ = vault_env
    content = "# System file content\n"

    # First write creates the file
    wrote1 = vault.write_system("Dashboard.md", content)
    assert wrote1 is True
    sys_file = vault_dir / "_system" / "Dashboard.md"
    assert sys_file.exists()
    mtime1 = sys_file.stat().st_mtime_ns

    # Second write with same content returns False and skips write
    wrote2 = vault.write_system("Dashboard.md", content)
    assert wrote2 is False
    mtime2 = sys_file.stat().st_mtime_ns
    assert mtime1 == mtime2

    # Modified content writes
    wrote3 = vault.write_system("Dashboard.md", content + "extra line\n")
    assert wrote3 is True


def test_obsidian_uri(vault_env):
    vault, vault_dir, _ = vault_env
    ref = NoteRef(
        articulate_id="01JDTEST",
        path=vault_dir / "Inbox" / "My Note.md",
        relative_path="Inbox/My Note.md",
        title="My Note",
    )
    uri = vault.obsidian_uri(ref)
    assert uri.startswith("obsidian://open?")
    assert "vault=" in uri
    assert "file=" in uri
