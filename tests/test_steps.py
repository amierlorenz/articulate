"""Tests for core.steps (read_encore, apply_flags, noop)."""
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from articulate.core.db import Database
from articulate.core.flags import Flags
from articulate.core.pipeline import RunContext
from articulate.core.steps import apply_flags, noop, read_encore
from articulate.core.taskqueue import TaskQueue
from articulate.core.vault import Vault

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "handoff"


@pytest.fixture
def steps_env(tmp_path: Path, monkeypatch):
    data_dir = tmp_path / "data"
    handoff_dir = tmp_path / "handoff"
    vault_dir = tmp_path / "vault"

    monkeypatch.setenv("ARTICULATE_DATA", str(data_dir))
    monkeypatch.setenv("ARTICULATE_HANDOFF", str(handoff_dir))

    db_path = data_dir / "articulate.sqlite3"
    db = Database(db_path)
    db.migrate()

    vault = Vault(vault_dir, db)
    taskqueue = TaskQueue(db)
    flags = Flags(db, vault, taskqueue)

    ctx = RunContext(
        run_id="run-step-1",
        job="nightly",
        now=datetime(2026, 11, 20, 2, 0, tzinfo=UTC),
        db=db,
        config=None,
        vault=vault,
        flags=flags,
        tasks=taskqueue,
    )
    return ctx, handoff_dir, db, vault


def test_noop_step(steps_env):
    ctx, _, _, _ = steps_env
    res = noop(ctx)
    assert res.done == 0
    assert res.held == 0
    assert res.failed == 0


def test_read_encore_processes_files_and_cleans_up(steps_env):
    ctx, handoff_dir, db, _ = steps_env
    from_encore = handoff_dir / "from-encore"
    from_encore.mkdir(parents=True, exist_ok=True)

    # Place state.json, library.json, attempts-1.jsonl, flag-1.json
    shutil.copy(FIXTURES_DIR / "valid_state.json", from_encore / "state.json")
    shutil.copy(FIXTURES_DIR / "valid_library.json", from_encore / "library.json")
    shutil.copy(FIXTURES_DIR / "valid_attempts.jsonl", from_encore / "attempts-exp1.jsonl")
    shutil.copy(FIXTURES_DIR / "valid_flag.json", from_encore / "flag-uuid1.json")

    # Place a dotfile (should be ignored)
    (from_encore / ".temp.tmp").write_text("ignore me")

    summary = read_encore(ctx)
    assert summary.failed == 0
    assert summary.done > 0

    # state.json and library.json should still exist (overwritten files, not deleted)
    assert (from_encore / "state.json").exists()
    assert (from_encore / "library.json").exists()

    # attempts and flag files should be deleted after ingestion (§3.7)
    assert not (from_encore / "attempts-exp1.jsonl").exists()
    assert not (from_encore / "flag-uuid1.json").exists()

    # Verify database snapshots
    with db.connect() as conn:
        state_snap = conn.execute("SELECT * FROM encore_snapshots WHERE schema = 'encore.state'").fetchone()
        assert state_snap is not None

        lib_snap = conn.execute("SELECT * FROM encore_snapshots WHERE schema = 'encore.library'").fetchone()
        assert lib_snap is not None


def test_read_encore_unknown_version_flags(steps_env):
    ctx, handoff_dir, db, _ = steps_env
    from_encore = handoff_dir / "from-encore"
    from_encore.mkdir(parents=True, exist_ok=True)

    # Copy unknown version
    shutil.copy(FIXTURES_DIR / "unknown_version.json", from_encore / "state.json")

    summary = read_encore(ctx)
    assert summary.failed > 0

    # Flag should be raised in flags or health
    with db.connect() as conn:
        flags_rows = conn.execute("SELECT * FROM flags WHERE kind LIKE '%encore%' OR kind LIKE '%schema%'").fetchall()
        assert len(flags_rows) > 0


def test_apply_flags_step(steps_env):
    ctx, _, _db, vault = steps_env
    # Add open flag
    flag_id = ctx.flags.add("test_kind", "Test flag", [("ok", "OK"), ("drop", "Drop")])
    ctx.flags.render_note()

    # Tick box
    note_path = vault.vault_dir / "Inbox" / "Intake flags.md"
    note_path.write_text(note_path.read_text().replace("[ ] OK", "[x] OK"))

    called = []
    ctx.flags.register("test_kind", lambda fid, ch, c: called.append((fid, ch)))

    summary = apply_flags(ctx)
    assert summary.done == 1
    assert len(called) == 1
    assert called[0] == (flag_id, "ok")
