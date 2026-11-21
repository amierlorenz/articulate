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
    assert [n["conceptId"] for n in doc["new"]] == [
        "csc301.paging",   # core, Test 3 (Memory) on Nov 30
        "mat305.mean",     # core, final Dec 4
        "csc301.thread",   # core, final Dec 7 (csc301.process is introduced)
        "csc301.tlb",      # standard, Nov 30
        "mat305.stderr",   # standard, Dec 4
        "csc301.history",  # background, room left
    ]
    assert [n["reason"] for n in doc["new"]] == ["core"] * 3 + ["standard"] * 2 + ["background"]
    assert doc["reviewScope"] == ["csc301", "mat305"]
    assert doc["intervalCaps"] == [{"courseId": "csc301", "maxDays": 30}, {"courseId": "mat305", "maxDays": 30}]
    assert doc["budgetMinutes"] == {"target": 20, "planned": 20}
    assert doc["kindsMix"] == {"freeRecallMin": 0.5}
    assert doc["readingShare"] == 0
    assert summary.done == 1 and summary.failed == 0
    assert _open_flags(ctx.db, NO_STATE_FLAG) == 0


def test_no_state_means_nothing_introduced_and_a_flag(env):
    ctx, out = env
    step(ctx)
    doc = json.loads(out.read_text())
    assert doc["new"][:4] == [
        {"conceptId": "csc301.paging", "promptIds": [], "reason": "core"},
        {"conceptId": "mat305.mean", "promptIds": [], "reason": "core"},
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
