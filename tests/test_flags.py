"""Tests for Weekly Flags (Acceptance check 0.14)."""
from datetime import UTC, datetime
from pathlib import Path

import pytest

from articulate.core.db import Database
from articulate.core.flags import Flags
from articulate.core.pipeline import RunContext
from articulate.core.taskqueue import TaskQueue
from articulate.core.vault import Vault


@pytest.fixture
def flags_env(tmp_path: Path):
    db_path = tmp_path / "test.sqlite3"
    db = Database(db_path)
    db.migrate()
    vault_dir = tmp_path / "vault"
    vault = Vault(vault_dir, db)
    taskqueue = TaskQueue(db)
    flags = Flags(db, vault, taskqueue)
    return flags, vault, db, taskqueue


def test_flags_render_and_tick_resolver(flags_env):
    flags, vault, db, taskqueue = flags_env
    ctx = RunContext(
        run_id="run-1",
        job="nightly",
        now=datetime(2026, 11, 23, 2, 0, tzinfo=UTC),
        db=db,
        config=None,
        vault=vault,
        flags=flags,
        tasks=taskqueue,
    )

    # Add two flags
    flag1_id = flags.add(
        "bad_quote",
        "Quote mismatch on Lec 2",
        [("keep", "keep"), ("merge", "merge into Lec 1"), ("drop", "drop")],
        item_id="item-10",
    )
    flag2_id = flags.add(
        "bad_quote",
        "Ambiguous term on Lec 3",
        [("keep", "keep"), ("drop", "drop")],
        item_id="item-20",
    )

    # Render note
    flags.render_note()
    flags_note = vault.vault_dir / "Inbox" / "Intake flags.md"
    assert flags_note.exists()
    content = flags_note.read_text(encoding="utf-8")
    assert f"<!-- flag:{flag1_id} -->" in content
    assert f"<!-- flag:{flag2_id} -->" in content
    assert "Quote mismatch on Lec 2" in content

    # Register resolver
    resolved_calls = []

    def dummy_resolver(fid: str, chosen: str, c: RunContext):
        resolved_calls.append((fid, chosen))

    flags.register("bad_quote", dummy_resolver)

    # Simulate Amier ticking box for flag 1: choice 'drop' (third box)
    lines = content.splitlines()
    new_lines = []
    for line in lines:
        if flag1_id in line:
            # Tick the drop box
            line = line.replace("[ ] drop", "[x] drop")
        new_lines.append(line)
    flags_note.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    # Tick processing on next run
    flags.process_ticks(ctx)

    assert len(resolved_calls) == 1
    assert resolved_calls[0] == (flag1_id, "drop")

    # Verify DB status
    with db.connect() as conn:
        row1 = conn.execute("SELECT status, chosen FROM flags WHERE flag_id = ?", (flag1_id,)).fetchone()
        assert row1["status"] == "resolved"
        assert row1["chosen"] == "drop"

        row2 = conn.execute("SELECT status FROM flags WHERE flag_id = ?", (flag2_id,)).fetchone()
        assert row2["status"] == "open"

    # Processing again does not call resolver a second time
    flags.process_ticks(ctx)
    assert len(resolved_calls) == 1

    # Carry-over: re-render note should now contain only flag2
    flags.render_note()
    updated_content = flags_note.read_text(encoding="utf-8")
    assert flag1_id not in updated_content
    assert flag2_id in updated_content


def test_flags_weekly_task_enqueued_when_flags_exist(flags_env):
    flags, vault, db, taskqueue = flags_env
    now = datetime(2026, 11, 23, 2, 0, tzinfo=UTC)  # 2026 week 48
    ctx = RunContext(
        run_id="run-1",
        job="nightly",
        now=now,
        db=db,
        config=None,
        vault=vault,
        flags=flags,
        tasks=taskqueue,
    )

    # When no open flags, no task enqueued
    flags.check_weekly_task(ctx)
    with db.connect() as conn:
        tasks = conn.execute("SELECT * FROM tasks").fetchall()
        assert len(tasks) == 0

    # Add an open flag
    flags.add("confusable", "Pair mean and median", [("keep", "keep"), ("drop", "drop")])
    flags.check_weekly_task(ctx)

    with db.connect() as conn:
        tasks = conn.execute("SELECT * FROM tasks WHERE external_id LIKE 'study:flags:%'").fetchall()
        assert len(tasks) == 1
        t = tasks[0]
        assert "Clear 1 intake flags" in t["title"] or "intake flags" in t["title"].lower()
        assert "obsidian://" in t["link"]

    # Calling check_weekly_task again in same week does not duplicate
    flags.check_weekly_task(ctx)
    with db.connect() as conn:
        tasks = conn.execute("SELECT * FROM tasks WHERE external_id LIKE 'study:flags:%'").fetchall()
        assert len(tasks) == 1
