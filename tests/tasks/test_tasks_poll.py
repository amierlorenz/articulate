"""Nightly Get Task State poll (1.24) and the registered step."""
from datetime import UTC, datetime, timedelta

from articulate.core.taskqueue import TaskRequest
from articulate.tasks.poll import poll_states
from articulate.tasks.send import ShortcutsClient, send_pending
from tests.tasks.conftest import VOICE_NOTE, add_item, archive_transcript, load_fixture, task_rows

NOW = datetime(2026, 11, 21, 7, 0, tzinfo=UTC)


def _sent(env, ext, sent_at=NOW - timedelta(days=1), mt_state="open"):
    with env.db.connect() as conn:
        conn.execute(
            """INSERT INTO tasks (external_id, title, band, state, master_todo_id, created_at, sent_at)
               VALUES (?, 'T', 'high', 'sent', 'ID', ?, ?);""",
            (ext, sent_at.isoformat(), sent_at.isoformat()),
        )
        conn.commit()
    if mt_state == "removed":
        env.shortcuts.set(tombstones=env.shortcuts.read()["tombstones"] + [f"articulate|{ext}"])
    elif mt_state != "unknown":
        s = env.shortcuts.read()
        s["tasks"][f"articulate|{ext}"] = {"id": "ID", "state": mt_state}
        env.shortcuts.write(s)


def test_poll_records_open_done_removed(env):
    client = ShortcutsClient(binary=env.shortcuts_bin)
    _sent(env, "a", mt_state="open")
    _sent(env, "b", mt_state="done")
    _sent(env, "c", mt_state="removed")
    _sent(env, "d", mt_state="unknown")
    poll_states(env.ctx(job="nightly", now=NOW), client)
    rows = task_rows(env.db)
    assert [rows[k]["state"] for k in "abcd"] == ["open", "done", "removed", "unknown"]
    assert rows["b"]["closed_at"] and rows["c"]["closed_at"]
    assert all(rows[k]["last_polled_at"] for k in "abcd")
    with env.db.connect() as conn:
        assert conn.execute("SELECT 1 FROM tombstones WHERE kind='task' AND key='c';").fetchone()


def test_poll_stops_at_done_removed_and_90_days(env):
    client = ShortcutsClient(binary=env.shortcuts_bin)
    _sent(env, "old", sent_at=NOW - timedelta(days=91))
    _sent(env, "b", mt_state="done")
    poll_states(env.ctx(job="nightly", now=NOW), client)
    poll_states(env.ctx(job="nightly", now=NOW), client)
    polled = [c["input"]["externalID"] for c in env.shortcuts.calls("Get Task State")]
    assert polled == ["b"]


def test_removed_task_is_never_resent(env):
    client = ShortcutsClient(binary=env.shortcuts_bin)
    _sent(env, "c", mt_state="removed")
    poll_states(env.ctx(job="nightly", now=NOW), client)
    env.queue.enqueue(TaskRequest(external_id="c", title="T", band="high"))  # idempotent: no new row
    send_pending(env.ctx(now=NOW), client)
    assert env.shortcuts.calls("Add Task") == []


def test_done_ticks_the_fallback_checkbox(env):
    client = ShortcutsClient(binary=env.shortcuts_bin)
    env.shortcuts.set(installed=False)
    env.queue.enqueue(TaskRequest(external_id="task:r:1", title="Renew my library card", band="high"))
    send_pending(env.ctx(now=NOW), client)
    env.shortcuts.set(installed=True)
    send_pending(env.ctx(now=NOW), client)
    s = env.shortcuts.read()
    s["tasks"]["articulate|task:r:1"]["state"] = "done"
    env.shortcuts.write(s)
    poll_states(env.ctx(job="nightly", now=NOW), client)
    text = (env.vault.vault_dir / "Inbox" / "Articulate tasks.md").read_text(encoding="utf-8")
    line = next(ln for ln in text.splitlines() if "task:r:1" in ln)
    assert line.startswith("- [x] Renew my library card")


def test_step_extracts_sends_and_polls_only_at_night(env, monkeypatch):
    from articulate import tasks

    monkeypatch.setenv("ARTICULATE_SHORTCUTS", env.shortcuts_bin)
    archive_transcript(env.data, load_fixture(VOICE_NOTE))
    add_item(env.db, item_id="item-voice-001", native_id="rec-voice-001")

    summary = tasks.step(env.ctx(job="watch", now=NOW))
    sent = {c["input"]["externalID"] for c in env.shortcuts.calls("Add Task")}
    assert sent == {"task:rec-voice-001:12", "task:rec-voice-001:31", "task:rec-voice-001:150"}
    assert env.shortcuts.calls("Get Task State") == []
    assert summary.done >= 3

    tasks.step(env.ctx(job="nightly", now=NOW))
    assert len(env.shortcuts.calls("Get Task State")) == 3


def test_registry_line():
    from articulate.registry import STEPS

    step = next(s for s in STEPS if s.name == "tasks.extract_send")
    assert step.entry == "articulate.tasks:step"
    assert step.jobs == {"watch", "nightly"}
    assert step.order == 130
