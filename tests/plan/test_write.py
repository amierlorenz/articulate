"""plan.daily step: inputs from SQLite and state.json, written to to-encore/plan.json (#66, 2.39)."""
import json
from datetime import UTC, datetime
from pathlib import Path

import jsonschema
import pytest

from articulate.core.config import Config, load_courses_config
from articulate.core.db import Database
from articulate.core.flags import Flags
from articulate.core.pipeline import RunContext
from articulate.plan.write import NO_STATE_FLAG, step

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "plan"
SCHEMA = Path(__file__).resolve().parents[2] / "schemas" / "encore.plan.v1.json"
PASS2 = datetime(2026, 11, 21, 8, 5, tzinfo=UTC)  # 03:05 Eastern


def _seed(db: Database) -> None:
    seed = json.loads((FIXTURES / "seed.json").read_text())
    with db.connect() as conn:
        for table in ("units", "exams", "concepts", "prompts"):
            for row in seed[table]:
                cols = ", ".join(row)
                marks = ", ".join("?" for _ in row)
                conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks});", tuple(row.values()))
        conn.commit()


def _store_state(db: Database) -> None:
    body = (FIXTURES / "state.json").read_text()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO encore_snapshots (schema, exported_at, read_at, body_json) VALUES (?, ?, ?, ?);",
            ("encore.state", "2026-11-21T08:02:00Z", "2026-11-21T08:03:00Z", body),
        )
        conn.commit()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICULATE_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("ARTICULATE_HANDOFF", str(tmp_path / "Handoff"))
    db = Database(tmp_path / "test.sqlite3")
    db.migrate()
    _seed(db)
    config = Config(courses=load_courses_config(FIXTURES / "courses.toml"))
    ctx = RunContext(run_id="01RUN", job="pass2", now=PASS2, db=db, config=config, flags=Flags(db))
    return ctx, tmp_path / "Handoff" / "to-encore" / "plan.json"


def _open_flags(db: Database, kind: str) -> int:
    with db.connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM flags WHERE kind = ? AND status = 'open';", (kind,)).fetchone()[0]


def test_plan_json_validates_against_the_committed_schema(env):
    ctx, out = env
    _store_state(ctx.db)
    step(ctx)
    doc = json.loads(out.read_text())
    jsonschema.validate(doc, json.loads(SCHEMA.read_text()))
    assert doc["header"]["schema"] == "encore.plan"
    assert doc["header"]["created_at"] == "2026-11-21T08:05:00Z"
    assert doc["validUntil"] == "2026-11-22T02:00:00-05:00"
    assert doc["date"] == "2026-11-21"


def test_not_yet_introduced_is_taken_from_state(env):
    ctx, out = env
    _store_state(ctx.db)
    summary = step(ctx)
    doc = json.loads(out.read_text())
    # Nov 21 is inside two ramps (#70): Test 3 on Nov 30 (day 9) and the MAT-305 final on Dec 4 (day 13).
    assert [n["conceptId"] for n in doc["new"]] == [
        "csc301.paging",   # core, Test 3 ramp (Memory), day 9
        "csc301.tlb",      # standard, Test 3 ramp
        "mat305.mean",     # core, MAT-305 final ramp, day 13
        "mat305.stderr",   # standard, MAT-305 final ramp
        "csc301.thread",   # core, CSC-301 final Dec 7, outside its ramp (csc301.process is introduced)
        "csc301.history",  # background, room left
    ]
    assert [n["reason"] for n in doc["new"]] == ["core", "standard", "core", "standard", "core", "background"]
    assert doc["examFocus"] == [{"examId": "csc301:test3", "rampDay": 9}, {"examId": "mat305:final", "rampDay": 13}]
    assert doc["reviewScope"] == ["csc301", "mat305"]
    assert doc["intervalCaps"] == [{"courseId": "csc301", "maxDays": 30}, {"courseId": "mat305", "maxDays": 30}]
    assert doc["budgetMinutes"] == {"target": 20, "planned": 35}
    assert doc["kindsMix"] == {"freeRecallMin": 0.5}
    assert doc["readingShare"] == 0
    assert summary.done == 1 and summary.failed == 0
    assert _open_flags(ctx.db, NO_STATE_FLAG) == 0


def test_no_state_means_nothing_introduced_and_a_flag(env):
    ctx, out = env
    step(ctx)
    doc = json.loads(out.read_text())
    assert doc["new"][:6] == [
        {"conceptId": "csc301.paging", "promptIds": [], "reason": "core"},
        {"conceptId": "csc301.tlb", "promptIds": [], "reason": "standard"},
        {"conceptId": "mat305.mean", "promptIds": [], "reason": "core"},
        {"conceptId": "mat305.stderr", "promptIds": [], "reason": "standard"},
        {"conceptId": "csc301.process", "promptIds": [], "reason": "core"},
        {"conceptId": "csc301.thread", "promptIds": [], "reason": "core"},
    ]
    assert _open_flags(ctx.db, NO_STATE_FLAG) == 1
    step(ctx)  # a second night without state doesn't pile up flags
    assert _open_flags(ctx.db, NO_STATE_FLAG) == 1


def test_plan_json_is_overwritten_never_deleted_and_recorded(env):
    ctx, out = env
    step(ctx)
    first = json.loads(out.read_text())["header"]["id"]
    ctx.now = datetime(2026, 11, 22, 8, 5, tzinfo=UTC)
    step(ctx)
    second = json.loads(out.read_text())
    assert second["header"]["id"] != first
    assert second["date"] == "2026-11-22"
    assert out.exists()
    assert [p.name for p in out.parent.iterdir()] == ["plan.json"]  # no tmp left behind
    with ctx.db.connect() as conn:
        rows = conn.execute("SELECT file_id, kind FROM handoff_files ORDER BY written_at;").fetchall()
    assert [(r["file_id"], r["kind"]) for r in rows] == [(first, "plan"), (second["header"]["id"], "plan")]


def test_registry_runs_plan_daily_last_in_pass2():
    from articulate.registry import STEPS

    (s,) = [s for s in STEPS if s.name == "plan.daily"]
    assert s.entry == "articulate.plan:step"
    assert s.jobs == {"pass2"}
    assert s.order == 380
    others = [x.order for x in STEPS if "pass2" in x.jobs and x.name not in ("plan.daily", "core.plan_hook")]
    assert all(o < 380 for o in others)


# --- Phase 3 inputs from SQLite (#70) ---------------------------------------------------------------------------

def _attempt(db: Database, attempt_id: str, concept_id: str, answered_at: str, session_kind: str = "plan") -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO attempts (attempt_id, prompt_id, concept_id, answered_at, grade, session_kind, recorded_at)"
            " VALUES (?, ?, ?, ?, 3, ?, ?);",
            (attempt_id, f"{concept_id}.flashcard.1", concept_id, answered_at, session_kind, answered_at),
        )
        conn.commit()


def _idle_tasks(db: Database) -> list[str]:
    with db.connect() as conn:
        rows = conn.execute("SELECT external_id FROM tasks WHERE external_id LIKE 'study:idle:%' ORDER BY 1;")
        return [r["external_id"] for r in rows.fetchall()]


def test_ramp_fields_reach_plan_json_and_validate(env):
    ctx, out = env
    _store_state(ctx.db)
    step(ctx)
    doc = json.loads(out.read_text())
    jsonschema.validate(doc, json.loads(SCHEMA.read_text()))
    assert doc["practiceTest"] == {"examId": "csc301:test3", "itemCount": 15, "freeRecallMin": 0.5}  # day 9
    assert doc["reviewQuiz"] is None
    assert doc["holdNewForExams"] == []
    # Introduced, nothing attempted yesterday: limit reviews, ramp exams first; one due review fits easily.
    assert doc["reviewLimit"] == 140
    assert doc["reviewPriority"] == ["csc301:test3", "mat305:final"]
    assert doc["newPaused"] is False


def test_studied_yesterday_means_no_review_limit(env):
    ctx, out = env
    _store_state(ctx.db)
    _attempt(ctx.db, "a1", "csc301.process", "2026-11-20T15:00:00Z")
    step(ctx)
    doc = json.loads(out.read_text())
    assert doc["reviewLimit"] is None and doc["reviewPriority"] == [] and doc["newPaused"] is False


def test_three_idle_days_enqueue_one_task_per_streak(env):
    ctx, _ = env
    _store_state(ctx.db)
    _attempt(ctx.db, "a1", "csc301.process", "2026-11-17T15:00:00Z")
    ctx.now = datetime(2026, 11, 20, 8, 5, tzinfo=UTC)     # Nov 18, 19 idle
    step(ctx)
    assert _idle_tasks(ctx.db) == []
    for day in (21, 22, 23):
        ctx.now = datetime(2026, 11, day, 8, 5, tzinfo=UTC)
        step(ctx)
    assert _idle_tasks(ctx.db) == ["study:idle:2026-11-18"]
    _attempt(ctx.db, "a2", "csc301.process", "2026-11-23T20:00:00Z")   # a session ends the streak
    for day in (25, 26, 27):
        ctx.now = datetime(2026, 11, day, 8, 5, tzinfo=UTC)
        step(ctx)
    assert _idle_tasks(ctx.db) == ["study:idle:2026-11-18", "study:idle:2026-11-24"]
    with ctx.db.connect() as conn:
        band = conn.execute("SELECT band, state FROM tasks WHERE external_id = 'study:idle:2026-11-18';").fetchone()
    assert (band["band"], band["state"]) == ("system", "pending")


def test_reissued_prompt_is_a_correction_first_in_new(env):
    ctx, out = env
    _store_state(ctx.db)
    with ctx.db.connect() as conn:
        conn.execute("UPDATE prompts SET status = 'retired' WHERE prompt_id = 'csc301.process.flashcard.1';")
        conn.execute(
            "INSERT INTO prompts (prompt_id, concept_id, kind, status, created_at)"
            " VALUES ('csc301.process.flashcard.2', 'csc301.process', 'flashcard', 'active', '2026-11-20');"
        )
        conn.commit()
    step(ctx)
    doc = json.loads(out.read_text())
    assert doc["new"][0] == {"conceptId": "csc301.process", "promptIds": ["csc301.process.flashcard.2"],
                             "reason": "correction"}
    assert len(doc["new"]) == 7   # the six regular concepts are untouched


def test_skipped_postclass_quiz_leads_new(env):
    ctx, out = env
    _store_state(ctx.db)
    with ctx.db.connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS postclass_quizzes (lecture_id TEXT PRIMARY KEY, course_key TEXT, event_id TEXT,"
            " recording_id TEXT, status TEXT, batch_id TEXT, plan_id TEXT, created_at TEXT, built_at TEXT);"
        )
        conn.executemany(
            "INSERT INTO postclass_quizzes (lecture_id, course_key, status, batch_id) VALUES (?, 'csc301', ?, ?);",
            [("csc301.l.2026-11-20", "built", "B1"), ("csc301.l.2026-11-21", "built", "B2")],  # today's: not yet
        )
        conn.execute("UPDATE prompts SET shipped_in = 'B1' WHERE concept_id IN ('mat305.stderr', 'csc301.history');")
        conn.execute("UPDATE prompts SET shipped_in = 'B2' WHERE concept_id = 'csc301.tlb';")
        conn.commit()
    step(ctx)
    doc = json.loads(out.read_text())
    assert [(n["conceptId"], n["reason"]) for n in doc["new"][:2]] == [
        ("csc301.history", "postclassSkipped"), ("mat305.stderr", "postclassSkipped"),
    ]
    assert [n["conceptId"] for n in doc["new"][2:]] == ["csc301.paging", "csc301.tlb", "mat305.mean", "csc301.thread"]


def test_contrast_prompts_are_read_from_sqlite(env):
    ctx, out = env
    state = json.loads((FIXTURES / "state.json").read_text())
    state["concepts"].append({"conceptId": "csc301.paging", "introducedAt": "2026-11-18T14:00:00Z",
                              "suspended": False})
    with ctx.db.connect() as conn:
        conn.execute(
            "INSERT INTO encore_snapshots (schema, exported_at, read_at, body_json) VALUES (?, ?, ?, ?);",
            ("encore.state", "2026-11-21T08:02:00Z", "2026-11-21T08:03:00Z", json.dumps(state)),
        )
        conn.execute(
            "INSERT INTO contrast_groups (group_id, course_key, name, member_ids_json, status)"
            " VALUES ('g1', 'csc301', 'units of work', '[\"csc301.process\", \"csc301.paging\"]', 'active');"
        )
        conn.execute(
            "INSERT INTO prompts (prompt_id, concept_id, kind, content_json, status) VALUES (?, ?, 'contrast', ?, 'active');",
            ("contrast:csc301.paging:csc301.process", "csc301.paging",
             json.dumps({"conceptIds": ["csc301.process", "csc301.paging"], "groupId": "g1"})),
        )
        conn.commit()
    _attempt(ctx.db, "a1", "csc301.process", "2026-11-20T15:00:00Z")
    _attempt(ctx.db, "a2", "csc301.paging", "2026-11-20T15:00:00Z")
    step(ctx)
    doc = json.loads(out.read_text())
    assert doc["new"][-1] == {"conceptId": "csc301.paging", "promptIds": ["contrast:csc301.paging:csc301.process"],
                              "reason": "core"}
    with ctx.db.connect() as conn:
        conn.execute("UPDATE contrast_groups SET status = 'dormant';")
        conn.commit()
    step(ctx)
    assert all(not n["promptIds"] for n in json.loads(out.read_text())["new"])
