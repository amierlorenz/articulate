"""Sending through master-todo's Add Task shortcut, the fallback list, and failures (1.22, 1.23, 1.25)."""
import pytest

from articulate.core.taskqueue import TaskRequest
from articulate.tasks.send import SendError, ShortcutsClient, send_pending
from tests.tasks.conftest import task_rows

FALLBACK = "Inbox/Articulate tasks.md"


@pytest.fixture
def client(env):
    return ShortcutsClient(binary=env.shortcuts_bin)


def _enqueue(env, ext="task:rec-1:12", title="Email Dana the lab budget", **kw):
    env.queue.enqueue(TaskRequest(external_id=ext, title=title, band=kw.pop("band", "high"), **kw))


def _fallback_text(env) -> str:
    return (env.vault.vault_dir / FALLBACK).read_text(encoding="utf-8")


def test_client_round_trips_json_through_shortcuts_run(env, client):
    assert client.available() is True
    res = client.add_task({"title": "T", "source": "articulate", "externalID": "task:r:1"})
    assert res["status"] == "created" and res["id"]
    assert client.get_state("articulate", "task:r:1") == "open"
    assert env.shortcuts.calls("Add Task")[0]["input"]["externalID"] == "task:r:1"


def test_client_raises_on_failure_and_reports_missing_shortcuts(env, client):
    env.shortcuts.set(fail=True)
    with pytest.raises(SendError):
        client.add_task({"title": "T", "source": "articulate", "externalID": "task:r:1"})
    env.shortcuts.set(installed=False)
    assert client.available() is False


def test_created_task_is_recorded_as_sent_with_all_fields(env, client):
    _enqueue(env, due_at="2026-11-26T00:00:00-05:00", has_due_time=False,
             notes="“line”\nEvening voice note · 2026-11-19 · @00:12",
             link="obsidian://open?vault=Vault&file=Inbox%2FEvening")
    summary = send_pending(env.ctx(), client)
    assert summary.done == 1
    row = task_rows(env.db)["task:rec-1:12"]
    assert row["state"] == "sent"
    assert row["master_todo_id"]
    assert row["sent_at"]
    sent = env.shortcuts.calls("Add Task")[0]["input"]
    assert sent == {
        "title": "Email Dana the lab budget", "source": "articulate", "externalID": "task:rec-1:12",
        "dueAt": "2026-11-26T00:00:00-05:00", "hasDueTime": False,
        "notes": "“line”\nEvening voice note · 2026-11-19 · @00:12",
        "link": "obsidian://open?vault=Vault&file=Inbox%2FEvening",
    }


def test_existing_in_master_todo_wins_over_the_ledger(env, client):
    s = env.shortcuts.read()
    s["tasks"]["articulate|task:rec-1:12"] = {"id": "ABC", "state": "done"}
    env.shortcuts.write(s)
    _enqueue(env)
    send_pending(env.ctx(), client)
    row = task_rows(env.db)["task:rec-1:12"]
    assert row["master_todo_id"] == "ABC"
    assert row["state"] == "done"


def test_skipped_means_tombstoned_and_never_sent_again(env, client):
    env.shortcuts.set(tombstones=["articulate|task:rec-1:12"])
    _enqueue(env)
    send_pending(env.ctx(), client)
    send_pending(env.ctx(), client)
    assert task_rows(env.db)["task:rec-1:12"]["state"] == "skipped"
    assert len(env.shortcuts.calls("Add Task")) == 1
    with env.db.connect() as conn:
        assert conn.execute("SELECT 1 FROM tombstones WHERE kind='task' AND key='task:rec-1:12';").fetchone()


def test_failed_send_stays_pending_and_three_failed_runs_raise_a_dashboard_row(env, client):
    _enqueue(env)
    env.shortcuts.set(fail=True)
    for _ in range(2):
        send_pending(env.ctx(), client)
    row = task_rows(env.db)["task:rec-1:12"]
    assert row["state"] == "pending" and row["send_failures"] == 2
    with env.db.connect() as conn:
        assert conn.execute("SELECT 1 FROM health WHERE kind='task_send_failing';").fetchone() is None

    send_pending(env.ctx(), client)
    with env.db.connect() as conn:
        h = conn.execute("SELECT * FROM health WHERE kind='task_send_failing';").fetchone()
    assert h is not None and h["interrupt"] == 0 and h["recovered_at"] is None
    with env.db.connect() as conn:  # dashboard row only: no health task through the broken channel
        assert conn.execute("SELECT 1 FROM tasks WHERE external_id LIKE 'health:%';").fetchone() is None

    env.shortcuts.set(fail=False)
    send_pending(env.ctx(), client)
    row = task_rows(env.db)["task:rec-1:12"]
    assert row["state"] == "sent" and row["send_failures"] == 0
    with env.db.connect() as conn:
        h = conn.execute("SELECT * FROM health WHERE kind='task_send_failing';").fetchone()
    assert h["recovered_at"] is not None


def test_without_the_shortcuts_tasks_go_to_the_fallback_list(env, client):
    env.shortcuts.set(installed=False)
    _enqueue(env, due_at="2026-11-20T15:00:00-05:00", has_due_time=True,
             notes="“Remind me to call the pharmacy tomorrow at 3pm.”\nEvening voice note · 2026-11-19 · @00:31",
             link="obsidian://open?vault=Vault&file=Inbox%2FEvening", title="Call the pharmacy")
    summary = send_pending(env.ctx(), client)
    assert summary.failed == 0
    assert task_rows(env.db)["task:rec-1:12"]["state"] == "fallback"
    text = _fallback_text(env)
    line = next(ln for ln in text.splitlines() if "<!-- task:task:rec-1:12 -->" in ln)
    assert line.startswith("- [ ] Call the pharmacy")
    assert "due 2026-11-20 15:00" in line
    assert "Remind me to call the pharmacy tomorrow at 3pm." in line
    assert "Evening voice note · 2026-11-19 · @00:31" in line
    assert "[note](obsidian://open?vault=Vault&file=Inbox%2FEvening)" in line
    assert "sent ✓" not in line
    with env.db.connect() as conn:  # not a failure: nothing counts toward the dashboard row
        assert conn.execute("SELECT 1 FROM health WHERE kind='task_send_failing';").fetchone() is None


def test_fallback_lines_are_sent_once_the_intent_works_and_marked_sent(env, client):
    env.shortcuts.set(installed=False)
    _enqueue(env)
    send_pending(env.ctx(), client)
    env.shortcuts.set(installed=True)
    send_pending(env.ctx(), client)
    assert task_rows(env.db)["task:rec-1:12"]["state"] == "sent"
    line = next(ln for ln in _fallback_text(env).splitlines() if "task:rec-1:12" in ln)
    assert "sent ✓" in line
    assert len(env.shortcuts.calls("Add Task")) == 1


def test_a_fallback_line_ticked_by_hand_is_done_and_never_sent(env, client):
    env.shortcuts.set(installed=False)
    _enqueue(env)
    send_pending(env.ctx(), client)
    p = env.vault.vault_dir / FALLBACK
    p.write_text(p.read_text(encoding="utf-8").replace("- [ ] Email", "- [x] Email"), encoding="utf-8")
    env.shortcuts.set(installed=True)
    send_pending(env.ctx(), client)
    assert task_rows(env.db)["task:rec-1:12"]["state"] == "done"
    assert env.shortcuts.calls("Add Task") == []
    assert "- [x] Email" in _fallback_text(env)


def test_health_and_study_chores_drain_through_the_same_sender(env, client):
    env.health.raise_("token_rejected", key="canvas", detail="401", interrupt=True)
    env.flags.add("tasks.medium", "x", [("send", "send"), ("drop", "drop")])
    env.flags.check_weekly_task(env.ctx())
    send_pending(env.ctx(), client)
    sent = {c["input"]["externalID"] for c in env.shortcuts.calls("Add Task")}
    assert any(e.startswith("health:token_rejected:") for e in sent)
    assert any(e.startswith("study:flags:") for e in sent)
    assert all(c["input"]["source"] == "articulate" for c in env.shortcuts.calls("Add Task"))


def test_flagged_dropped_and_low_rows_are_never_sent(env, client):
    _enqueue(env, ext="a")
    _enqueue(env, ext="b")
    _enqueue(env, ext="c")
    with env.db.connect() as conn:
        conn.execute("UPDATE tasks SET state='flagged' WHERE external_id='a';")
        conn.execute("UPDATE tasks SET state='dropped' WHERE external_id='b';")
        conn.commit()
    send_pending(env.ctx(), client)
    assert [c["input"]["externalID"] for c in env.shortcuts.calls("Add Task")] == ["c"]


def test_unparseable_output_counts_as_a_failure(env, client, tmp_path):
    bad = tmp_path / "bad-shortcuts"
    bad.write_text("#!/bin/sh\nif [ \"$1\" = list ]; then echo 'Add Task'; echo 'Get Task State'; exit 0; fi\n"
                   "for a; do case $prev in -o) echo 'not json' > \"$a\";; esac; prev=$a; done\n")
    bad.chmod(0o755)
    _enqueue(env)
    send_pending(env.ctx(), ShortcutsClient(binary=str(bad)))
    row = task_rows(env.db)["task:rec-1:12"]
    assert row["state"] == "pending" and row["send_failures"] == 1
