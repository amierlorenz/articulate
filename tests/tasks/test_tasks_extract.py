"""Extraction: what counts, fields, bands, flags, dedupe, once per recording (1.18–1.21)."""
from datetime import UTC, datetime, timedelta

from articulate.core.vault import NoteRef
from articulate.tasks.extract import PROMPT_VERSION, extract_pending
from tests.tasks.conftest import VOICE_NOTE, add_item, archive_transcript, load_fixture, task_rows

REC = "rec-voice-001"
ITEM = "item-voice-001"


def _setup_voice_note(env, **item_kw):
    archive_transcript(env.data, load_fixture(VOICE_NOTE))
    add_item(env.db, item_id=ITEM, native_id=REC, **item_kw)


def _open_flags(env):
    with env.db.connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM flags WHERE status = 'open' ORDER BY created_at;").fetchall()]


def test_first_person_commitments_become_high_band_pending_tasks(env):
    _setup_voice_note(env)
    extract_pending(env.ctx())
    rows = task_rows(env.db)

    budget = rows[f"task:{REC}:12"]
    assert budget["title"] == "Email Dana the lab budget"
    assert budget["band"] == "high"
    assert budget["state"] == "pending"
    assert budget["source"] == "articulate"
    assert budget["prompt_version"] == PROMPT_VERSION
    assert budget["origin_item_id"] == ITEM
    assert budget["due_at"] == "2026-11-26T00:00:00-05:00"
    assert budget["has_due_time"] == 0
    assert budget["notes"] == (
        "“I'll email Dana the lab budget by Thursday.”\nEvening voice note · 2026-11-19 · @00:12"
    )

    pharmacy = rows[f"task:{REC}:31"]
    assert pharmacy["due_at"] == "2026-11-20T15:00:00-05:00"
    assert pharmacy["has_due_time"] == 1


def test_never_tasks_delegated_musings_or_unquoted(env):
    _setup_voice_note(env)
    extract_pending(env.ctx())
    titles = {r["title"] for r in task_rows(env.db).values()}
    assert "Send the slides" not in titles  # Amier asking someone else
    assert "Learn to cook" not in titles  # musing
    assert "Book the dentist" not in titles  # quote not in the transcript
    with env.db.connect() as conn:
        reasons = {r["title"]: r["reason"] for r in conn.execute("SELECT * FROM task_drops;")}
    assert reasons == {
        "Send the slides": "delegated",
        "Learn to cook": "musing",
        "Book the dentist": "quote_not_found",
    }


def test_due_only_when_the_quote_says_it(env):
    _setup_voice_note(env)
    extract_pending(env.ctx())
    card = task_rows(env.db)[f"task:{REC}:150"]
    assert card["due_at"] is None  # model said "Friday" but the line never does
    assert card["has_due_time"] == 0


def test_medium_and_unanswered_go_to_weekly_flags_low_is_logged_only(env):
    _setup_voice_note(env)
    extract_pending(env.ctx())
    rows = task_rows(env.db)

    poster = rows[f"task:{REC}:58"]
    assert (poster["band"], poster["state"]) == ("medium", "flagged")
    riley = rows[f"task:{REC}:110"]
    assert (riley["band"], riley["state"]) == ("medium", "flagged")
    gym = rows[f"task:{REC}:130"]
    assert (gym["band"], gym["state"]) == ("low", "dropped")

    flags = _open_flags(env)
    assert [f["kind"] for f in flags] == ["tasks.medium", "tasks.medium"]
    assert {f["item_id"] for f in flags} == {f"task:{REC}:58", f"task:{REC}:110"}
    assert any(f["subject"].startswith("Unanswered request: ") for f in flags)
    for f in flags:
        assert "confidence" not in f["subject"].lower()
    # Confidence never appears on the task itself
    for r in rows.values():
        assert "confidence" not in (r["notes"] or "").lower()
        assert "medium" not in r["title"].lower()


def test_ticking_send_on_a_medium_flag_queues_it(env):
    from articulate.tasks.extract import apply_flag_choices

    _setup_voice_note(env)
    extract_pending(env.ctx())
    flags = {f["item_id"]: f["flag_id"] for f in _open_flags(env)}
    now = datetime(2026, 11, 21, 7, 0, tzinfo=UTC).isoformat()
    with env.db.connect() as conn:
        conn.execute("UPDATE flags SET status='resolved', chosen='send', resolved_at=? WHERE flag_id=?;",
                     (now, flags[f"task:{REC}:58"]))
        conn.execute("UPDATE flags SET status='resolved', chosen='drop', resolved_at=? WHERE flag_id=?;",
                     (now, flags[f"task:{REC}:110"]))
        conn.commit()
    apply_flag_choices(env.ctx())
    rows = task_rows(env.db)
    assert rows[f"task:{REC}:58"]["state"] == "pending"
    assert rows[f"task:{REC}:110"]["state"] == "dropped"


def test_link_points_at_the_inbox_note_when_one_exists(env):
    _setup_voice_note(env)
    ref = env.vault.create_note("Inbox", "Evening voice note", {"pocket_id": REC}, "body\n",
                                kind="inbox", source_item_id=ITEM)
    assert isinstance(ref, NoteRef)
    extract_pending(env.ctx())
    link = task_rows(env.db)[f"task:{REC}:12"]["link"]
    assert link == env.vault.obsidian_uri(ref)
    assert link.startswith("obsidian://open?vault=Vault&file=Inbox/Evening%20voice%20note")


def test_no_note_means_no_link(env):
    _setup_voice_note(env)
    extract_pending(env.ctx())
    assert task_rows(env.db)[f"task:{REC}:12"]["link"] is None


def test_each_recording_is_extracted_once(env):
    _setup_voice_note(env)
    extract_pending(env.ctx())
    extract_pending(env.ctx())
    assert len(env.runner.extract_calls) == 1
    with env.db.connect() as conn:
        row = conn.execute("SELECT * FROM task_extractions WHERE item_id = ?;", (ITEM,)).fetchone()
    assert row["prompt_version"] == PROMPT_VERSION
    assert row["recording_id"] == REC


def test_private_class_and_unfinished_items_are_never_read(env):
    archive_transcript(env.data, load_fixture(VOICE_NOTE))
    add_item(env.db, item_id="p", native_id=REC, privacy="private")
    add_item(env.db, item_id="c", native_id=REC + "-c", privacy="class")
    add_item(env.db, item_id="h", native_id=REC + "-h", status="held")
    add_item(env.db, item_id="m", native_id=REC + "-m", payload={"merged": True})
    add_item(env.db, item_id="o", native_id=REC + "-o", source="canvas")
    extract_pending(env.ctx())
    assert env.runner.calls == []
    assert task_rows(env.db) == {}


def test_private_prompt_material_is_personal_and_local(env):
    _setup_voice_note(env)
    extract_pending(env.ctx())
    req = env.runner.extract_calls[0]
    assert req.step == "personal_text"
    assert req.material.value == "personal"
    assert req.json_schema is not None
    embeds = [c for c in env.runner.calls if c.embed]
    assert embeds and all(c.step == "embed_personal" for c in embeds)


def test_span_only_reads_its_window(env):
    archive_transcript(env.data, load_fixture(VOICE_NOTE))
    add_item(env.db, item_id="span-1", native_id=f"{REC}:tail", kind="span",
             payload={"recording_id": REC, "start_sec": 3600, "end_sec": 3740})
    env.runner.output = {"tasks": []}
    extract_pending(env.ctx())
    prompt = env.runner.extract_calls[0].parts[-1].text
    assert "wrapping up" in prompt
    assert "lab budget" not in prompt


def test_held_model_leaves_the_item_for_next_run(env):
    _setup_voice_note(env)
    env.runner.held = "ollama_down"
    summary = extract_pending(env.ctx())
    assert summary.held == 1
    env.runner.held = None
    extract_pending(env.ctx())
    assert f"task:{REC}:12" in task_rows(env.db)


def test_near_duplicate_of_a_recent_open_task_is_dropped_and_logged(env):
    from articulate.tasks.extract import store_embedding

    sent_at = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)  # 8 days before the run
    with env.db.connect() as conn:
        conn.execute(
            """INSERT INTO tasks (external_id, title, band, state, created_at, sent_at)
               VALUES ('task:old:1', 'Email Dana the budget', 'high', 'open', ?, ?);""",
            (sent_at.isoformat(), sent_at.isoformat()),
        )
        conn.commit()
    store_embedding(env.db, "task:old:1", [0.0, 1.0, 0.0])
    env.runner.vectors["Email Dana the lab budget"] = [0.0, 0.99, 0.05]
    _setup_voice_note(env)
    extract_pending(env.ctx())

    row = task_rows(env.db)[f"task:{REC}:12"]
    assert row["state"] == "dropped"
    runs = (env.vault.vault_dir / "_system" / "Runs.md").read_text(encoding="utf-8")
    assert "near-duplicate" in runs and "task:old:1" in runs


def test_old_or_closed_tasks_do_not_count_as_duplicates(env):
    from articulate.tasks.extract import store_embedding

    old = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)  # 19 days before
    recent = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
    with env.db.connect() as conn:
        conn.execute("""INSERT INTO tasks (external_id, title, band, state, created_at, sent_at)
                        VALUES ('task:old:1', 'x', 'high', 'open', ?, ?);""",
                     (old.isoformat(), old.isoformat()))
        conn.execute("""INSERT INTO tasks (external_id, title, band, state, created_at, sent_at)
                        VALUES ('task:done:1', 'y', 'high', 'done', ?, ?);""",
                     (recent.isoformat(), recent.isoformat()))
        conn.commit()
    store_embedding(env.db, "task:old:1", [0.0, 1.0, 0.0])
    store_embedding(env.db, "task:done:1", [0.0, 1.0, 0.0])
    env.runner.vectors["Email Dana the lab budget"] = [0.0, 1.0, 0.0]
    _setup_voice_note(env)
    extract_pending(env.ctx(now=old + timedelta(days=19)))
    assert task_rows(env.db)[f"task:{REC}:12"]["state"] == "pending"


def test_titles_are_capped_at_80_chars(env):
    _setup_voice_note(env)
    long_title = "Email Dana " + "the very detailed lab budget breakdown " * 4
    env.runner.output = {"tasks": [{
        "title": long_title, "quote": "I'll email Dana the lab budget by Thursday.",
        "kind": "commitment", "confidence": "high", "due_spoken": None}]}
    extract_pending(env.ctx())
    title = task_rows(env.db)[f"task:{REC}:12"]["title"]
    assert len(title) <= 80
    assert long_title.startswith(title.rstrip("…").rstrip())
