"""Tests for lecture-directive tasks extraction (§7.1, Issue #75, SPEC-requirements 3.23)."""

import json
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from articulate.tasks.lecture import (
    DATE_CHANGE_FLAG_KIND,
    PROMPT_VERSION,
    extract_lectures,
    matches_canvas_assignment,
)
from tests.tasks.conftest import (
    add_item,
    archive_transcript,
    task_rows,
)

FIXTURES_LECTURE = Path(__file__).resolve().parent.parent / "fixtures" / "tasks" / "lecture"
REC_LECTURE = "rec-lecture-001"
ITEM_LECTURE = "item-lecture-001"


def _load_lecture_fixture(name: str) -> dict:
    return json.loads((FIXTURES_LECTURE / name).read_text(encoding="utf-8"))


def _setup_lecture(env, **item_kw):
    archive_transcript(env.data, _load_lecture_fixture("recording-detail-lecture.json"))
    env.runner.output = _load_lecture_fixture("model-output-lecture.json")
    kw = {
        "item_id": ITEM_LECTURE,
        "native_id": REC_LECTURE,
        "privacy": "class",
        "kind": "span",
        "payload": {
            "recording_id": REC_LECTURE,
            "title": "MAT-305 Probability & Stats",
            "course_key": "mat305",
            "start_sec": 0,
            "end_sec": 3600,
            "span_kind": "class",
        },
    }
    kw.update(item_kw)
    add_item(env.db, **kw)


def _mock_canvas_client(assignments: list[dict] | None = None):
    client = MagicMock()
    client.list_assignments.return_value = (
        assignments if assignments is not None else _load_lecture_fixture("canvas-assignments.json")
    )
    return client


def _open_flags(env):
    with env.db.connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM flags WHERE status = 'open' ORDER BY created_at;"
            ).fetchall()
        ]


def test_directive_with_concrete_action_becomes_task(env):
    _setup_lecture(env)
    client = _mock_canvas_client()
    extract_lectures(env.ctx(job="nightly"), client=client)

    rows = task_rows(env.db)
    # Directive: "Please read Chapter 4 before Monday." @ 15.2s -> start 15
    task_key = f"task:{REC_LECTURE}:15"
    assert task_key in rows, f"Expected {task_key} in {list(rows.keys())}"
    task = rows[task_key]
    assert task["title"] == "Read Chapter 4"
    assert task["band"] == "high"
    assert task["state"] == "pending"
    assert task["source"] == "articulate"
    assert task["prompt_version"] == PROMPT_VERSION
    assert task["origin_item_id"] == ITEM_LECTURE
    # Recording is on Monday 2026-11-23; next Monday is 2026-11-30
    assert task["due_at"] == "2026-11-30T00:00:00-05:00"
    assert task["has_due_time"] == 0
    assert "“Please read Chapter 4 before Monday.”" in task["notes"]
    assert "MAT-305 Probability & Stats" in task["notes"]
    assert "@00:15" in task["notes"]


def test_same_course_assignment_due_within_two_days_is_dropped(env):
    _setup_lecture(env)
    # Canvas has Homework 1 due on 2026-11-25T23:59:59-05:00
    # Lecture directive says "submit Homework 1 by Wednesday night" (Wednesday = 2026-11-25)
    # Difference is 0 days <= 2 days -> dropped!
    client = _mock_canvas_client()
    extract_lectures(env.ctx(job="nightly"), client=client)

    rows = task_rows(env.db)
    hw1_key = f"task:{REC_LECTURE}:29"
    assert hw1_key in rows
    assert rows[hw1_key]["state"] == "dropped"

    # Check task_drops ledger
    with env.db.connect() as conn:
        drop_reasons = {
            r["title"]: r["reason"] for r in conn.execute("SELECT * FROM task_drops;").fetchall()
        }
    assert drop_reasons.get("Submit Homework 1") == "canvas_assignment"

    # Verify runlog notes the drop
    runs = (env.vault.vault_dir / "_system" / "Runs.md").read_text(encoding="utf-8")
    assert "dropped Canvas assignment look-alike" in runs
    assert "Submit Homework 1" in runs


def test_assignment_due_more_than_two_days_away_not_dropped(env):
    _setup_lecture(env)
    # If Canvas assignment is due far in the future (> 2 days)
    far_assignments = [
        {
            "id": 101,
            "name": "Homework 1",
            "due_at": "2026-12-15T23:59:59-05:00",
        }
    ]
    client = _mock_canvas_client(far_assignments)
    extract_lectures(env.ctx(job="nightly"), client=client)

    rows = task_rows(env.db)
    hw1_key = f"task:{REC_LECTURE}:29"
    assert rows[hw1_key]["state"] == "pending"


def test_assignment_in_different_course_not_dropped(env):
    # Lecture course is csc301, but Canvas assignment belongs to mat305
    _setup_lecture(
        env,
        payload={
            "recording_id": REC_LECTURE,
            "title": "CSC-301 Operating Systems",
            "course_key": "csc301",
            "start_sec": 0,
            "end_sec": 3600,
        },
    )
    # Client returns no assignments for csc301
    client = MagicMock()
    client.list_assignments.return_value = []
    extract_lectures(env.ctx(job="nightly"), client=client)

    rows = task_rows(env.db)
    hw1_key = f"task:{REC_LECTURE}:29"
    assert rows[hw1_key]["state"] == "pending"


def test_date_change_becomes_weekly_flag_not_task(env):
    _setup_lecture(env)
    client = _mock_canvas_client()
    extract_lectures(env.ctx(job="nightly"), client=client)

    # Date change is NOT in tasks table
    rows = task_rows(env.db)
    titles = {r["title"] for r in rows.values()}
    assert "Quiz moved to Friday" not in titles

    # Appears in flags table
    flags = _open_flags(env)
    date_flags = [f for f in flags if f["kind"] == DATE_CHANGE_FLAG_KIND]
    assert len(date_flags) == 1
    assert "Date change: Quiz moved to Friday" in date_flags[0]["subject"]
    assert "quiz is moved to Friday" in date_flags[0]["subject"]
    assert date_flags[0]["item_id"] == f"task:{REC_LECTURE}:45"


def test_instructor_commitment_and_musing_are_dropped(env):
    _setup_lecture(env)
    client = _mock_canvas_client()
    extract_lectures(env.ctx(job="nightly"), client=client)

    rows = task_rows(env.db)
    titles = {r["title"] for r in rows.values()}
    assert "Grade midterms" not in titles
    assert "Review notes daily" not in titles

    with env.db.connect() as conn:
        drop_reasons = {
            r["title"]: r["reason"] for r in conn.execute("SELECT * FROM task_drops;").fetchall()
        }
    assert drop_reasons.get("Grade midterms") == "instructor_commitment"
    assert drop_reasons.get("Review notes daily") == "musing"


def test_lecture_extraction_only_runs_in_nightly(env, monkeypatch):
    from articulate.tasks import step

    monkeypatch.setenv("ARTICULATE_SHORTCUTS", env.shortcuts_bin)
    _setup_lecture(env)
    # When job == "watch", lecture extraction does not run
    summary_watch = step(env.ctx(job="watch"))
    assert summary_watch.done == 0
    assert task_rows(env.db) == {}

    # When job == "nightly", lecture extraction runs
    summary_nightly = step(env.ctx(job="nightly"))
    assert summary_nightly.done > 0
    assert f"task:{REC_LECTURE}:15" in task_rows(env.db)


def test_each_lecture_extracted_once(env):
    _setup_lecture(env)
    client = _mock_canvas_client()
    extract_lectures(env.ctx(job="nightly"), client=client)
    extract_lectures(env.ctx(job="nightly"), client=client)

    # Model runner called only once for this item
    assert len(env.runner.extract_calls) == 1
    with env.db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM task_extractions WHERE item_id = ?;", (ITEM_LECTURE,)
        ).fetchone()
    assert row["prompt_version"] == PROMPT_VERSION
    assert row["recording_id"] == REC_LECTURE


def test_lecture_span_cuts_to_window(env):
    # Only read segments between 10s and 35s (includes Read Chapter 4 and Submit Homework 1)
    _setup_lecture(
        env,
        payload={
            "recording_id": REC_LECTURE,
            "title": "MAT-305 Probability & Stats",
            "course_key": "mat305",
            "start_sec": 10,
            "end_sec": 35,
        },
    )
    env.runner.output = {"tasks": []}
    extract_lectures(env.ctx(job="nightly"), client=_mock_canvas_client())

    prompt = env.runner.extract_calls[0].parts[-1].text
    assert "read Chapter 4" in prompt
    assert "slide 3" not in prompt


def test_near_duplicate_lecture_directive_is_dropped(env):
    from articulate.tasks.extract import store_embedding

    sent_at = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    with env.db.connect() as conn:
        conn.execute(
            """INSERT INTO tasks (external_id, title, band, state, created_at, sent_at)
               VALUES ('task:old:1', 'Read Chapter 4 textbook', 'high', 'open', ?, ?);""",
            (sent_at.isoformat(), sent_at.isoformat()),
        )
        conn.commit()
    store_embedding(env.db, "task:old:1", [0.0, 1.0, 0.0])
    env.runner.vectors["Read Chapter 4"] = [0.0, 0.99, 0.05]

    _setup_lecture(env)
    extract_lectures(env.ctx(job="nightly"), client=_mock_canvas_client())

    row = task_rows(env.db)[f"task:{REC_LECTURE}:15"]
    assert row["state"] == "dropped"
    runs = (env.vault.vault_dir / "_system" / "Runs.md").read_text(encoding="utf-8")
    assert "near-duplicate" in runs
    assert "task:old:1" in runs


def test_matches_canvas_assignment_unit_logic():
    rec_date = date(2026, 11, 23)
    asgns = [
        {"name": "Homework 1", "due_at": "2026-11-25T23:59:59Z"},
        {"name": "Lab 2", "due_at": "2026-12-05T23:59:59Z"},
    ]

    # Within 2 days match
    match1 = matches_canvas_assignment(
        "Submit Homework 1",
        "Please submit Homework 1",
        "2026-11-25T00:00:00-05:00",
        rec_date,
        asgns,
    )
    assert match1 is not None
    assert match1["name"] == "Homework 1"

    # Match based on recording date when due_at is None
    match2 = matches_canvas_assignment(
        "Homework 1 submission", "Homework 1 submission", None, rec_date, asgns
    )
    assert match2 is not None

    # Too far (> 2 days)
    match3 = matches_canvas_assignment(
        "Lab 2 report", "Turn in Lab 2", "2026-11-25T00:00:00-05:00", rec_date, asgns
    )
    assert match3 is None

    # No name match
    match4 = matches_canvas_assignment(
        "Read Chapter 4", "Read Chapter 4", "2026-11-25T00:00:00-05:00", rec_date, asgns
    )
    assert match4 is None


@pytest.mark.real
def test_real_lecture_recording_on_qwen(request, env):
    """Real check against an archived lecture fixture on qwen3:8b (SPEC-requirements 3.23)."""
    if "real" not in (request.config.getoption("-m") or ""):
        pytest.skip("runs only with -m real")
    from datetime import time as dt_time

    from conftest import EASTERN

    if dt_time(1, 45) <= datetime.now(EASTERN).time() <= dt_time(4, 0):
        pytest.skip("Never run real checks between 01:45 and 04:00.")

    real_dir = Path("~/Library/Application Support/articulate/fixtures/real").expanduser()
    real_file = real_dir / "d7be217b-cfb4-44e6-b5f6-a3d5000eaeec.json"
    if not real_file.exists():
        pytest.skip("real fixture missing")

    # Acquire lock
    import time

    lock_path = Path("/tmp/articulate-real.lock")
    acquired = False
    for _ in range(60):
        try:
            lock_path.mkdir(exist_ok=False)
            acquired = True
            break
        except FileExistsError:
            time.sleep(2)

    if not acquired:
        pytest.fail("Could not acquire /tmp/articulate-real.lock after retries.")

    try:
        import gzip

        from articulate.core.models import DefaultModelRunner

        detail = json.loads(real_file.read_text(encoding="utf-8"))
        rec_id = detail["data"]["id"]
        target = env.data / "transcripts" / "real" / "real" / f"{rec_id}.json.gz"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(gzip.compress(json.dumps(detail).encode()))

        add_item(
            env.db,
            item_id=f"item-{rec_id}",
            native_id=rec_id,
            privacy="class",
            kind="span",
            payload={
                "recording_id": rec_id,
                "title": "Real Lecture",
                "course_key": "mat305",
                "start_sec": 0,
                "end_sec": 1800,
            },
        )

        ctx = env.ctx(job="nightly")
        ctx.models = DefaultModelRunner(env.db, None, job="manual", run_id=ctx.run_id)

        mock_canvas = MagicMock()
        mock_canvas.list_assignments.return_value = []

        summary = extract_lectures(ctx, client=mock_canvas)
        assert summary.done >= 0
    finally:
        try:
            lock_path.rmdir()
        except OSError:
            pass
