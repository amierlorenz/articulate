"""Ambient audio: podcast and tutorial advice never becomes Amier's task; real commitments still do (#112)."""
import pytest

from articulate.tasks.extract import PROMPT, PROMPT_VERSION, extract_pending, gate
from articulate.tasks.lecture import extract_lectures
from tests.tasks.conftest import VOICE_NOTE, add_item, archive_transcript, load_fixture, task_rows
from tests.tasks.test_tasks_lecture import REC_LECTURE, _mock_canvas_client, _setup_lecture

PODCAST = "recording-detail-podcast.json"
PODCAST_OUTPUT = "model-output-podcast.json"
REC = "rec-podcast-001"
ITEM = "item-podcast-001"


def _setup_podcast(env, output: dict | None = None):
    archive_transcript(env.data, load_fixture(PODCAST))
    add_item(env.db, item_id=ITEM, native_id=REC)
    env.runner.output = output if output is not None else load_fixture(PODCAST_OUTPUT)


def _drops(env) -> dict[str, str]:
    with env.db.connect() as conn:
        return {r["title"]: r["reason"] for r in conn.execute("SELECT * FROM task_drops;")}


def test_podcast_advice_yields_zero_tasks_even_when_the_model_mislabels_it(env):
    _setup_podcast(env)
    extract_pending(env.ctx())

    assert task_rows(env.db) == {}
    assert _open_flags_count(env) == 0
    assert _drops(env) == {
        "Create a thumbnail with motion blur": "no_commitment_marker",
        "Put my face on the left third of thumbnails": "second_person_advice",
        "Export videos at 4K": "not_accepted",
        "Redo the video intro under ten seconds": "no_commitment_marker",
        "Add captions to videos": "advice",
        "Batch record three videos": "second_person_advice",
    }
    with env.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM task_extractions WHERE item_id = ?;",
                            (ITEM,)).fetchone()[0] == 1


def test_amier_committing_to_podcast_advice_is_still_a_task(env):
    detail = load_fixture(PODCAST)
    detail["data"]["transcript"]["segments"].append(
        {"start": 120.4, "end": 126.0, "text": "Okay, I'll try the motion blur thumbnail on my next upload."})
    archive_transcript(env.data, detail)
    add_item(env.db, item_id=ITEM, native_id=REC)
    output = load_fixture(PODCAST_OUTPUT)
    output["tasks"].append({
        "title": "Try a motion blur thumbnail on the next upload",
        "quote": "Okay, I'll try the motion blur thumbnail on my next upload.",
        "kind": "commitment", "confidence": "high", "due_spoken": None})
    env.runner.output = output
    extract_pending(env.ctx())

    rows = task_rows(env.db)
    assert list(rows) == [f"task:{REC}:120"]
    assert rows[f"task:{REC}:120"]["state"] == "pending"


def test_first_person_voice_note_still_extracts_cleanly(env):
    archive_transcript(env.data, load_fixture(VOICE_NOTE))
    add_item(env.db, item_id="item-voice-001", native_id="rec-voice-001")
    extract_pending(env.ctx())
    rows = task_rows(env.db)
    assert {k.rsplit(":", 1)[1] for k in rows} == {"12", "31", "58", "110", "130", "150"}
    assert set(_drops(env).values()) == {"delegated", "musing", "quote_not_found"}


def test_class_directives_survive_alongside_an_ambient_podcast(env):
    _setup_podcast(env)
    extract_pending(env.ctx())
    _setup_lecture(env)
    extract_lectures(env.ctx(job="nightly"), client=_mock_canvas_client())

    rows = task_rows(env.db)
    assert not any(k.startswith(f"task:{REC}:") for k in rows)
    directive = rows[f"task:{REC_LECTURE}:15"]  # "Please read Chapter 4 before Monday."
    assert (directive["title"], directive["state"]) == ("Read Chapter 4", "pending")


@pytest.mark.parametrize(("kind", "quote", "following", "reason"), [
    ("commitment", "I'll email Dana the lab budget by Thursday.", "", None),
    ("commitment", "I’m going to fix the bike.", "", None),
    ("commitment", "Remind me to call the pharmacy.", "", None),
    ("commitment", "Action item: send the deck to Priya.", "", None),
    ("commitment", "Add task: renew passport.", "", None),
    ("commitment", "Create a thumbnail with motion blur.", "", "no_commitment_marker"),
    ("commitment", "You should always shoot in natural light.", "", "no_commitment_marker"),
    ("accepted_request", "Can you review the poster draft?", "Yeah, I can do that.", None),
    ("accepted_request", "Make sure you export at 4K.", "Next tip: lighting.", "not_accepted"),
    ("accepted_request", "Can you print the flyers?", "Sure, tonight.", None),
    ("unanswered_request", "Riley wanted me to check the shared calendar.", "", None),
    ("unanswered_request", "Amier, can you grab the projector?", "", None),
    ("unanswered_request", "You want to batch record three videos.", "", "second_person_advice"),
    ("delegated", "Can you send me the slides, Sam?", "", None),  # dropped earlier as delegated
])
def test_gate(kind, quote, following, reason):
    assert gate(kind, quote, following) == reason


def test_prompt_requires_markers_and_rejects_advice():
    assert PROMPT_VERSION == "task-v2"
    for marker in ("I need to", "Remind me to", "Action item:", "Add task:"):
        assert marker in PROMPT
    assert "`advice`" in PROMPT
    assert "You should" in PROMPT
    assert "create a thumbnail with motion blur" in " ".join(PROMPT.lower().split())


def _open_flags_count(env) -> int:
    with env.db.connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM flags WHERE status = 'open';").fetchone()[0]
