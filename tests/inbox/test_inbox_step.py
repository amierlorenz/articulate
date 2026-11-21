"""The inbox.notes step end to end on fixtures (SPEC-requirements 1.10–1.17)."""
import re

from conftest import EASTERN

from articulate.core.pipeline import Privacy
from articulate.core.vault import _parse_frontmatter
from articulate.inbox import note_for_item, run_inbox

IDEA_NOTE = {
    "title": "Highlights become morning questions",
    "paraphrase": "Turn reading-app highlights into next-morning questions, only for highlights with a comment.",
    "quotes": [
        "What if every highlight turned into a question the next morning",
        "maybe only the ones I wrote a comment on",
    ],
    "decisions": [],
    "open_questions": [],
}


def run(env, **kw):
    return run_inbox(env.ctx(), tz=EASTERN, **kw)


# --- Gate (1.10) -------------------------------------------------------------------------------------------


def test_no_transcript_gets_no_note_and_no_model_call(env):
    env.archive("inbox/no-transcript.json")
    item = env.emit("recording", "inbox-empty", Privacy.PERSONAL)
    run(env)
    assert env.models.requests == []
    assert env.notes() == []
    assert env.ledger(item)["outcome"] == "skip_no_transcript"


def test_under_15_words_gets_no_note_and_no_model_call(env):
    env.archive("pocket/recording-detail-null-duration.json")  # 5 words
    item = env.emit("recording", "rec-003", Privacy.PERSONAL)
    run(env)
    assert env.models.requests == []
    assert env.ledger(item)["outcome"] == "skip_short"


def test_playback_is_gated_out(env):
    env.archive("inbox/playback.json")
    item = env.emit("recording", "inbox-playback", Privacy.PERSONAL)
    env.models.gate = lambda text: {"decision": "skip", "kind": "content", "reason": "podcast playback"}
    run(env)
    assert len(env.models.calls("gate")) == 1
    assert env.models.calls("note") == []
    assert env.notes() == []
    assert env.ledger(item)["outcome"] == "skip_gate"


def test_tasks_only_gets_no_note(env):
    env.archive("inbox/tasks-only.json")
    item = env.emit("recording", "inbox-tasks", Privacy.PERSONAL)
    env.models.gate = lambda text: {"decision": "tasks_only", "kind": "other", "reason": "only reminders"}
    run(env)
    assert env.models.calls("note") == []
    assert env.notes() == []
    assert env.ledger(item)["outcome"] == "tasks_only"


# --- Note shape (1.11) -------------------------------------------------------------------------------------


def test_note_frontmatter_and_body(env):
    env.archive("inbox/idea.json")
    item = env.emit("recording", "inbox-idea", Privacy.PERSONAL)
    env.models.note = [IDEA_NOTE]
    summary = run(env)

    [path] = env.notes()
    assert path.parent.name == "Inbox"
    assert path.name == "Highlights become morning questions.md"
    text = path.read_text()
    fm = _parse_frontmatter(text)
    assert fm["tags"] == ["fleeting", "agent"]
    assert fm["agent"] is True
    assert fm["articulate_id"]
    assert fm["pocket_id"] == "inbox-idea"
    assert fm["kind"] == "idea"
    assert fm["created"] == "2026-11-20"
    assert fm["recorded"] == "2026-11-18T10:00-05:00"
    assert "event" not in fm
    body = text.split("---\n", 2)[2]
    assert IDEA_NOTE["paraphrase"] in body
    # quotes carry the ≈ time of where code found them, not a model-written one
    assert '"What if every highlight turned into a question the next morning" ≈0:00:20' in body
    assert re.search(r'"maybe only the ones I wrote a comment on" ≈0:01:0\d', body)
    assert summary.done == 1
    assert env.ledger(item)["outcome"] == "note"


def test_every_model_call_is_local_personal_text(env):
    env.archive("inbox/idea.json")
    env.emit("recording", "inbox-idea", Privacy.PERSONAL)
    env.models.note = [IDEA_NOTE]
    run(env)
    assert env.models.requests
    for req in env.models.requests:
        assert req.step == "personal_text"
        assert req.material == Privacy.PERSONAL


def test_more_than_three_quotes_are_cut_to_three(env):
    env.archive("inbox/idea.json")
    env.emit("recording", "inbox-idea", Privacy.PERSONAL)
    four = dict(IDEA_NOTE, quotes=IDEA_NOTE["quotes"] + [
        "Okay so I keep coming back to this idea",
        "The hard part is picking which highlights deserve a question",
    ])
    env.models.note = [four]
    run(env)
    [path] = env.notes()
    assert path.read_text().count(" ≈0:") == 3


def test_one_runs_line_per_recording(env):
    env.archive("inbox/idea.json")
    env.archive("inbox/playback.json")
    env.emit("recording", "inbox-idea", Privacy.PERSONAL)
    env.emit("recording", "inbox-playback", Privacy.PERSONAL)
    env.models.gate = lambda text: (
        {"decision": "skip", "kind": "content", "reason": "playback"}
        if "bicycle" in text
        else {"decision": "note", "kind": "idea", "reason": "idea"}
    )
    env.models.note = [IDEA_NOTE]
    run(env)
    lines = (env.vault_dir / "_system" / "Runs.md").read_text().splitlines()
    inbox_lines = [ln for ln in lines if " inbox " in ln]
    assert len(inbox_lines) == 2
    assert any("inbox-idea" in ln and "note" in ln for ln in inbox_lines)
    assert any("inbox-playback" in ln and "skip" in ln for ln in inbox_lines)
    # no transcript text in Runs.md
    assert not any("highlight" in ln.lower() for ln in lines)


def test_done_items_are_not_redone(env):
    env.archive("inbox/idea.json")
    env.emit("recording", "inbox-idea", Privacy.PERSONAL)
    env.models.note = [IDEA_NOTE]
    run(env)
    n = len(env.models.requests)
    run(env)
    assert len(env.models.requests) == n
    assert len(env.notes()) == 1


def test_tasks_can_find_the_note_for_an_item(env):
    env.archive("inbox/idea.json")
    item = env.emit("recording", "inbox-idea", Privacy.PERSONAL)
    env.models.note = [IDEA_NOTE]
    run(env)
    ref = note_for_item(env.db, item)
    assert ref is not None
    assert ref.relative_path == "Inbox/Highlights become morning questions.md"
    assert note_for_item(env.db, "pocket:nothing") is None


# --- Meetings (1.13) ---------------------------------------------------------------------------------------


def test_meeting_variant(env):
    env.archive("inbox/meeting.json")
    env.emit("recording", "inbox-meeting", Privacy.PERSONAL)
    seen = {}

    def matcher(start, end, ctx):
        seen["window"] = (start, end)
        return {
            "title": "Showcase poster sync",
            "start": "2026-11-18T11:00:00-05:00",
            "end": "2026-11-18T11:30:00-05:00",
            "attendees": ["Jordan Lee", "Sam Ortiz"],
        }

    env.models.gate = lambda text: {"decision": "note", "kind": "idea", "reason": "x"}
    env.models.note = [{
        "title": "Poster plan",
        "paraphrase": "Poster plan for the showcase: printer deadline and the open demo-laptop question.",
        "quotes": ["the poster goes to the printer by Friday"],
        "decisions": ["Poster goes to the printer by Friday; Jordan owns the layout"],
        "open_questions": ["Can the demo laptop stay on the table all afternoon?"],
    }]
    run(env, meeting_matcher=matcher)

    assert seen["window"][0].isoformat() == "2026-11-18T16:00:00+00:00"
    [path] = env.notes()
    assert path.name == "Showcase poster sync.md"
    text = path.read_text()
    fm = _parse_frontmatter(text)
    assert fm["kind"] == "meeting"
    assert fm["event"] == "2026-11-18 11:00–11:30"
    assert "## Decisions\n- Poster goes to the printer by Friday; Jordan owns the layout" in text
    assert "## Open questions\n- Can the demo laptop stay on the table all afternoon?" in text
    assert "Attendees: Jordan Lee, Sam Ortiz" in text
    assert "[[" not in text
    # the meeting context reaches the note prompt
    assert "Showcase poster sync" in "\n".join(p.text or "" for p in env.models.calls("note")[0].parts)


def test_non_meeting_note_has_no_decision_sections(env):
    env.archive("inbox/idea.json")
    env.emit("recording", "inbox-idea", Privacy.PERSONAL)
    env.models.note = [dict(IDEA_NOTE, decisions=["something"], open_questions=["q"])]
    run(env, meeting_matcher=lambda s, e, c: None)
    text = env.notes()[0].read_text()
    assert "## Decisions" not in text


# --- Quote check (1.12) ------------------------------------------------------------------------------------

BAD = dict(IDEA_NOTE, quotes=["every highlight becomes a flashcard at night"])


def test_failed_quote_is_retried_once_with_the_error(env):
    env.archive("inbox/idea.json")
    item = env.emit("recording", "inbox-idea", Privacy.PERSONAL)
    env.models.note = [BAD, IDEA_NOTE]
    run(env)
    calls = env.models.calls("note")
    assert len(calls) == 2
    retry_text = "\n".join(p.text or "" for p in calls[1].parts)
    assert "every highlight becomes a flashcard at night" in retry_text
    assert "not found" in retry_text
    assert len(env.notes()) == 1
    assert env.ledger(item)["outcome"] == "note"


def test_second_failure_holds_and_flags(env):
    env.archive("inbox/idea.json")
    item = env.emit("recording", "inbox-idea", Privacy.PERSONAL)
    env.models.note = [BAD, BAD]
    summary = run(env)
    assert env.notes() == []
    row = env.ledger(item)
    assert row["status"] == "held"
    assert row["hold_reason"] == "quote_check_failed"
    with env.db.connect() as conn:
        flags = conn.execute("SELECT * FROM flags WHERE kind = 'inbox.quote_check';").fetchall()
    assert len(flags) == 1
    assert flags[0]["item_id"] == item
    assert summary.held == 1

    # held items wait for the flag: no new model calls
    n = len(env.models.requests)
    run(env)
    assert len(env.models.requests) == n


def test_flag_retry_reruns_and_drop_gives_up(env):
    env.archive("inbox/idea.json")
    env.archive("inbox/meeting.json")
    a = env.emit("recording", "inbox-idea", Privacy.PERSONAL)
    b = env.emit("recording", "inbox-meeting", Privacy.PERSONAL)
    env.models.note = lambda text: BAD
    run(env)
    with env.db.connect() as conn:
        conn.execute("UPDATE flags SET status='resolved', chosen='retry' WHERE item_id = ?;", (a,))
        conn.execute("UPDATE flags SET status='resolved', chosen='drop' WHERE item_id = ?;", (b,))
        conn.commit()
    env.models.note = lambda text: IDEA_NOTE if "highlight" in text else BAD
    run(env)
    assert env.ledger(a)["outcome"] == "note"
    assert env.ledger(b)["status"] == "dropped"
    assert len(env.notes()) == 1


def test_ollama_down_holds_and_retries_next_pass_without_a_flag(env):
    env.archive("inbox/idea.json")
    item = env.emit("recording", "inbox-idea", Privacy.PERSONAL)
    env.models.raise_held = "ollama_down"
    run(env)
    row = env.ledger(item)
    assert (row["status"], row["hold_reason"]) == ("held", "ollama_down")
    env.models.raise_held = None
    env.models.note = [IDEA_NOTE]
    run(env)
    assert env.ledger(item)["outcome"] == "note"
    with env.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM flags;").fetchone()[0] == 0


# --- Long recordings (1.14) --------------------------------------------------------------------------------


def test_long_recording_is_chunked_into_one_note(env):
    env.archive("inbox/long.json")  # 40 min
    env.emit("recording", "inbox-long", Privacy.PERSONAL)

    def chunk_note(text):
        m = re.search(r"In minute (\d+) I talked", text)
        i = m.group(1)
        return {
            "title": f"Chunk {i}",
            "paraphrase": f"Part {i} of the plan.",
            "quotes": [f"habit {i} matters more than talent"],
            "decisions": [],
            "open_questions": [],
        }

    env.models.note = chunk_note
    env.models.merge = {
        "title": "Semester plan habits",
        "paraphrase": "Habits beat talent across the semester plan.",
        # quotes are numbered 1–4 (one per chunk); the model asks for 7 points, code keeps 5
        "points": [{"text": f"Point {k}", "quote": (k - 1) % 4 + 1} for k in range(1, 8)],
        "decisions": [],
        "open_questions": [],
    }
    run(env)
    assert len(env.models.calls("note")) == 4
    assert len(env.models.calls("merge")) == 1
    [path] = env.notes()
    text = path.read_text()
    assert path.name == "Semester plan habits.md"
    # at most 5 points, each with one checked quote and its time
    points = [ln for ln in text.splitlines() if ln.startswith("- Point")]
    assert len(points) == 5
    assert '- Point 1 — "habit 0 matters more than talent" ≈0:00:' in text
    assert '- Point 2 — "habit 10 matters more than talent" ≈0:10:' in text


# --- Class tails (1.15) ------------------------------------------------------------------------------------


def test_class_tail_uses_only_its_window_and_says_where_it_was_cut_from(env):
    env.archive("inbox/class-tail.json")
    item = env.emit(
        "span",
        "inbox-lecture:4500",
        Privacy.PERSONAL,
        {"recording_id": "inbox-lecture", "start_sec": 4500.0, "end_sec": 4620.0},
    )
    env.models.note = [{
        "title": "Group flashcards by mistake",
        "paraphrase": "Group flashcards by the mistake made to see repeating confusions.",
        "quotes": ["grouping flashcards by the mistake I made"],
        "decisions": [],
        "open_questions": [],
    }]
    run(env)
    for req in env.models.requests:
        text = "\n".join(p.text or "" for p in req.parts)
        assert "Lecture part" not in text
    [path] = env.notes()
    text = path.read_text()
    fm = _parse_frontmatter(text)
    assert fm["pocket_id"] == "inbox-lecture"
    assert fm["recorded"] == "2026-11-17T11:15-05:00"
    assert "Cut from PHY 203 lecture" in text
    assert "≈1:15:" in text
    assert env.ledger(item)["outcome"] == "note"


# --- Privacy, tombstones, where writes go (1.16, 1.17) -----------------------------------------------------


def test_private_and_class_recordings_are_never_read(env, monkeypatch):
    env.archive("inbox/idea.json")
    env.archive("pocket/recording-detail-lecture.json")
    env.emit("recording", "inbox-idea", Privacy.PRIVATE)
    env.emit("recording", "rec-001", Privacy.CLASS)
    import importlib

    step_mod = importlib.import_module("articulate.inbox.step")

    def boom(recording_id):
        raise AssertionError(f"read {recording_id}")

    monkeypatch.setattr(step_mod, "load_archived", boom)
    run(env)
    assert env.models.requests == []
    assert env.notes() == []


def test_deleted_note_is_tombstoned_and_not_recreated(env):
    env.archive("inbox/idea.json")
    item = env.emit("recording", "inbox-idea", Privacy.PERSONAL)
    env.models.note = lambda text: IDEA_NOTE
    run(env)
    [path] = env.notes()
    path.unlink()
    # even if the item were processed again (ledger lost), the note stays gone
    with env.db.connect() as conn:
        conn.execute("DELETE FROM inbox_items;")
        conn.commit()
    run(env)
    assert env.notes() == []
    assert env.ledger(item)["outcome"] == "tombstoned"


def test_waits_when_the_transcript_is_not_archived_yet(env):
    item = env.emit("recording", "inbox-idea", Privacy.PERSONAL)
    run(env)
    assert env.ledger(item) is None or env.ledger(item)["status"] == "pending"
    env.archive("inbox/idea.json")
    env.models.note = [IDEA_NOTE]
    run(env)
    assert env.ledger(item)["outcome"] == "note"


def test_writes_only_inside_inbox_and_system(env):
    for f in ("idea.json", "meeting.json", "playback.json"):
        env.archive(f"inbox/{f}")
    env.emit("recording", "inbox-idea", Privacy.PERSONAL)
    env.emit("recording", "inbox-meeting", Privacy.PERSONAL)
    env.emit("recording", "inbox-playback", Privacy.PERSONAL)
    env.models.note = lambda text: IDEA_NOTE if "highlight" in text else BAD
    run(env)
    written = [p.relative_to(env.vault_dir).parts[0] for p in env.vault_dir.rglob("*") if p.is_file()]
    assert written
    assert set(written) <= {"Inbox", "_system"}
