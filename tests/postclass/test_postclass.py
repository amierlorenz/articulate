"""Fixture tests for the post-class quiz (§7.1, #71)."""
import gzip
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from articulate.calendar.client import CalendarEvent
from articulate.core.clock import pacific_date
from articulate.core.config import (
    ArticulateConfig,
    Config,
    CourseConfig,
    CoursesConfig,
    VaultConfig,
)
from articulate.core.db import Database
from articulate.core.handoff_schemas import ImportBatch, Plan, load_handoff_file
from articulate.core.models.quota import get_remaining_quota
from articulate.core.models.runner import Held, ModelRequest, ModelResult
from articulate.core.pipeline import RunContext
from articulate.pocket.client import PocketClient
from articulate.postclass import build
from articulate.postclass.source import ClassWindow, find_class_window, takeover_due
from articulate.registry import STEPS

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "postclass"
FLASH_LITE = "gemini-3.5-flash-lite"
NOW = datetime(2026, 11, 21, 0, 15, tzinfo=UTC)  # 19:15 Eastern, 30 min after MAT 305 ended
LECTURE = "mat305.l.2026-11-20"
BAYES = "mat305.bayes-theorem"


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def all_events() -> list[CalendarEvent]:
    return [CalendarEvent.from_dict(e, calendar_name="Classes") for e in fixture("events.json")]


def events_from(evts: list[CalendarEvent]):
    return lambda ctx, lo, hi: evts


class FakeRunner:
    """postclass_quiz returns the fixture; embeddings make Bayes texts identical; the judge says `same` for Bayes.

    Quota follows the real ledger rule: `remaining` = rpd − counted calls − reserve (pass2 only), and a quiz call with
    nothing remaining raises Held like the runner does.
    """

    def __init__(self, db: Database, job: str, now: datetime, quiz: dict | None = None):
        self.db, self.job, self.now = db, job, now
        self.quiz = quiz if quiz is not None else fixture("quiz-response.json")
        self.calls: list[ModelRequest] = []

    def remaining(self, model: str) -> int:
        return get_remaining_quota(self.db, None, model, job=self.job, now=self.now)

    def run(self, req: ModelRequest) -> ModelResult:
        self.calls.append(req)
        if req.step == "postclass_quiz":
            if self.remaining(FLASH_LITE) <= 0:
                raise Held("quota")
            return ModelResult(text=json.dumps(self.quiz), parsed=self.quiz, model=FLASH_LITE, provider="gemini")
        if req.step == "embed_class":
            vecs = []
            for p in req.parts:
                t = (p.text or "").lower()
                vecs.append([1.0, 0.0, 0.0] if "bayes" in t else [0.0, float(len(t) % 7 + 1), float(len(t))])
            return ModelResult(vectors=vecs, model="gemini-embedding-2", provider="gemini")
        if req.step == "library_judge":
            text = req.parts[0].text or ""
            matches = []
            if f"[{BAYES}]" in text:
                matches = [{"id": BAYES, "relation": "same", "reason": "same theorem", "adds": []}]
            parsed = {"candidates": [{"candidate": 0, "matches": matches}]}
            return ModelResult(text=json.dumps(parsed), parsed=parsed, model=FLASH_LITE, provider="gemini")
        raise AssertionError(f"unexpected step {req.step}")

    def steps(self) -> list[str]:
        return [c.step for c in self.calls]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICULATE_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("ARTICULATE_HANDOFF", str(tmp_path / "handoff"))
    (tmp_path / "handoff" / "to-encore").mkdir(parents=True)

    def no_pocket(*a, **k):
        raise AssertionError("post-class must not call Pocket")

    monkeypatch.setattr(PocketClient, "_request_raw", no_pocket)
    db = Database(tmp_path / "data" / "articulate.sqlite3")
    db.migrate()
    with db.connect() as conn:
        conn.execute("INSERT INTO courses (course_key, name, active) VALUES ('mat305', 'Probability', 1);")
        conn.execute("INSERT INTO units VALUES ('mat305.u.unit-2', 'mat305', 'Unit 2', 2);")
        conn.execute("INSERT INTO units VALUES ('mat305.u.unit-3', 'mat305', 'Unit 3: Inference', 3);")
        conn.execute(
            """INSERT INTO concepts (concept_id, course_key, unit_id, kind, title, details_json, tier, status,
                                     confidence_note, created_at, updated_at, shipped_in)
               VALUES (?, 'mat305', 'mat305.u.unit-2', 'method', 'Bayes theorem', '{}', 'core', 'active',
                       'Reverse a conditional probability.', '2026-11-01', '2026-11-01', 'old-batch');""",
            (BAYES,),
        )
        conn.execute(
            """INSERT INTO prompts (prompt_id, concept_id, kind, content_json, status, created_at, shipped_in)
               VALUES (?, ?, 'shortAnswer', '{"question":"q","modelAnswer":"a","keyPoints":[]}', 'active',
                       '2026-11-01', 'old-batch');""",
            (f"{BAYES}.shortAnswer.1", BAYES),
        )
        conn.commit()
    return tmp_path, db


def make_ctx(db: Database, tmp_path: Path, *, now: datetime = NOW, job: str = "watch") -> RunContext:
    cfg = Config(
        courses=CoursesConfig(
            courses={
                "mat305": CourseConfig(canvas_id=1, name="Probability", active=True),
                "csc430": CourseConfig(canvas_id=2, name="Old course", active=False),
            }
        ),
        articulate=ArticulateConfig(vault=VaultConfig(path=str(tmp_path / "vault"))),
    )
    ctx = RunContext(run_id="run-1", job=job, now=now, db=db, config=cfg)
    ctx.models = FakeRunner(db, job, now)
    return ctx


def archive_class_recording(tmp_path: Path, db: Database) -> None:
    """What pocket.poll leaves behind: the archived detail and the class span item (window 300–5400 s)."""
    detail = fixture("recording-detail.json")
    path = tmp_path / "data" / "transcripts" / "2026" / "11" / "rec-pc-1.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(json.dumps(detail).encode()))
    payload = {"recording_id": "rec-pc-1", "title": "MAT 305 lecture", "start_sec": 300.0, "end_sec": 5400.0,
               "course_key": "mat305", "span_kind": "class"}
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO items (item_id, source, kind, native_id, course_key, privacy, stage, status, payload_json,
                                  created_at, updated_at)
               VALUES ('pocket:rec-pc-1:class:300', 'pocket', 'span', 'rec-pc-1:class:300', 'mat305', 'class',
                       'entry', 'pending', ?, ?, ?);""",
            (json.dumps(payload), NOW.isoformat(), NOW.isoformat()),
        )
        conn.commit()


def row(db: Database, lecture_id: str = LECTURE):
    with db.connect() as conn:
        return conn.execute("SELECT * FROM postclass_quizzes WHERE lecture_id = ?;", (lecture_id,)).fetchone()


def to_encore(tmp_path: Path) -> Path:
    return tmp_path / "handoff" / "to-encore"


# --- registry --------------------------------------------------------------------------------------------------


def test_registry_line():
    step = next(s for s in STEPS if s.name == "postclass.build")
    assert step.entry == "articulate.postclass.build:step"
    assert step.order == 140
    assert step.jobs == {"watch"}
    pocket = next(s for s in STEPS if s.name == "pocket.poll")
    assert pocket.order < step.order


# --- 1. no ended class → exit without Pocket; one quiz per lecture --------------------------------------------


def test_no_class_ended_exits_without_pocket_or_models(env, monkeypatch):
    tmp_path, db = env
    ctx = make_ctx(db, tmp_path)
    archive_class_recording(tmp_path, db)

    def no_archive(*a, **k):
        raise AssertionError("no ended class: the archive is not read")

    monkeypatch.setattr("articulate.postclass.source.load_archived", no_archive)
    not_ended = [e for e in all_events() if e.id in ("evt-mat305-1119", "evt-mat305-1121", "evt-csc430-1120")]

    summary = build.run(ctx, events=events_from(not_ended), launch=lambda cmd: None)

    assert summary.done == summary.held == summary.failed == 0
    assert ctx.models.calls == []
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM postclass_quizzes;").fetchone()[0] == 0
    assert list(to_encore(tmp_path).iterdir()) == []


def test_one_quiz_per_lecture(env):
    tmp_path, db = env
    archive_class_recording(tmp_path, db)
    launches: list[list[str]] = []

    ctx = make_ctx(db, tmp_path)
    first = build.run(ctx, events=events_from(all_events()), launch=launches.append)
    assert first.done == 1  # lecture + review the same day are one lecture; csc430 is inactive; old/future skipped
    assert ctx.models.steps().count("postclass_quiz") == 1

    again = make_ctx(db, tmp_path, now=NOW.replace(minute=45))
    second = build.run(again, events=events_from(all_events()), launch=launches.append)
    assert second.done == 0
    assert again.models.calls == []

    r = row(db)
    assert r["status"] == "built"
    assert r["event_id"] == "evt-mat305-1120"
    assert r["recording_id"] == "rec-pc-1"
    with db.connect() as conn:
        assert [x[0] for x in conn.execute("SELECT lecture_id FROM postclass_quizzes;")] == [LECTURE]
    assert len(list(to_encore(tmp_path).glob("postclass-*.json"))) == 1
    assert len(list(to_encore(tmp_path).glob("import-*.json"))) == 1
    assert len(launches) == 1


# --- 2. quotes checked exactly against the archived class window ----------------------------------------------


def test_quotes_checked_against_class_window(env):
    tmp_path, db = env
    archive_class_recording(tmp_path, db)
    ctx = make_ctx(db, tmp_path)
    lecture = build.ended_lectures(ctx, all_events())[0]
    window = find_class_window(ctx, lecture)
    assert isinstance(window, ClassWindow)
    # the class window is the event's own: 17:30 → 18:55 local, i.e. 300 s → 5400 s on the recording
    assert (window.start_sec, window.end_sec) == (300.0, 5400.0)

    ok = build.check_quote(window, 1200, 1210, "a TYPE I error is rejecting the null hypothesis... when")
    assert ok == (1200.0, 1210.0)  # case, whitespace and punctuation are canonicalised
    assert build.check_quote(window, 1200, 1210, "a type I error is rejecting the null when it is true") is None
    assert build.check_quote(window, 400, 410, "rejecting the null hypothesis") is None  # wrong time
    assert build.check_quote(window, 10, 20, "watch the game last night") is None  # lead-in, before class
    assert build.check_quote(window, 5600, 5610, "the variance of a sum") is None  # tail, after class + 10 min
    assert build.check_quote(window, 400, 410, "Bayes") is None  # too short to check

    build.run(ctx, events=events_from(all_events()), launch=lambda cmd: None)
    batch_file = next(to_encore(tmp_path).glob("import-*.json"))
    batch = load_handoff_file(batch_file, ImportBatch, expected_schema="encore.import", expected_version=2)
    concepts = {c.title: c for u in batch.courses[0].units for c in u.concepts}
    assert "Central limit theorem" not in concepts  # paraphrased quote
    assert "Variance of a sum" not in concepts  # quote from the tail
    type1 = concepts["Type I error"]
    quotes = [c.quote for c in type1.details.explanationCitations]
    assert quotes == ["a type I error is rejecting the null hypothesis when it is actually true"]  # "it's" failed
    cit = type1.details.explanationCitations[0]
    assert cit.status == "verified" and cit.verifiedBy == "text"
    assert (cit.startSec, cit.endSec) == (1200.0, 1210.0)
    assert cit.sourceFileId == batch.courses[0].sourceFiles[0].id
    assert batch.courses[0].sourceFiles[0].origin == "pocket"


def test_parse_items_mostly_free_recall_and_failing_items_dropped(env):
    tmp_path, db = env
    archive_class_recording(tmp_path, db)
    ctx = make_ctx(db, tmp_path)
    window = find_class_window(ctx, build.ended_lectures(ctx, all_events())[0])
    quiz = fixture("quiz-response.json")
    extra_mc = json.loads(json.dumps(quiz["items"][4]))
    extra_mc["concept"]["title"] = "p-value again"
    quiz["items"] = [quiz["items"][4], extra_mc, quiz["items"][0]]
    items = build.parse_items(quiz, window, "f.test")
    assert [i.title for i in items] == ["p-value", "Bayes' theorem"]  # one multiple choice at most


# --- 3. matched vs new concepts; batch written before the post-class plan that names it -----------------------


def test_matched_and_new_concepts_and_handoff_order(env, monkeypatch):
    tmp_path, db = env
    archive_class_recording(tmp_path, db)
    ctx = make_ctx(db, tmp_path)
    written: list[str] = []
    real_batch, real_file = build.write_import_batch, build.write_handoff_file

    def spy_batch(batch, *a, **k):
        written.append(f"import:{batch.header.id}")
        return real_batch(batch, *a, **k)

    def spy_file(path, model):
        written.append(f"file:{Path(path).name}")
        return real_file(path, model)

    monkeypatch.setattr(build, "write_import_batch", spy_batch)
    monkeypatch.setattr(build, "write_handoff_file", spy_file)
    launches: list[list[str]] = []
    monkeypatch.setattr(build, "_open_encore", lambda cmd: launches.append(cmd))

    summary = build.run(ctx, events=events_from(all_events()), launch=lambda cmd: (written.append("open"),
                                                                                    launches.append(cmd)))
    assert summary.done == 1

    r = row(db)
    assert written[0] == f"import:{r['batch_id']}"
    assert written[1] == f"file:postclass-{LECTURE}.json"
    assert written[2] == "open"
    assert launches == [["open", "-g", "-b", "com.amierlorenz.Encore"]]

    batch = load_handoff_file(
        to_encore(tmp_path) / f"import-{r['batch_id']}.json", ImportBatch,
        expected_schema="encore.import", expected_version=2,
    )
    plan = load_handoff_file(
        to_encore(tmp_path) / f"postclass-{LECTURE}.json", Plan, expected_schema="encore.plan", expected_version=1
    )
    assert plan.kind == "postclass"
    assert plan.importBatch == batch.header.id == r["batch_id"]
    assert plan.header.id == r["plan_id"] == f"postclass-{LECTURE}"
    assert plan.reviewScope == ["mat305"]
    assert str(plan.date) == "2026-11-20"
    assert plan.validUntil == datetime(2026, 11, 21, 7, 0, tzinfo=UTC)  # 02:00 Eastern

    with db.connect() as conn:
        concepts = {c["title"]: dict(c) for c in conn.execute("SELECT * FROM concepts;")}
        prompts = {p["prompt_id"]: dict(p) for p in conn.execute("SELECT * FROM prompts;")}

    # matched: the existing concept keeps its id, tier and unit and gains a prompt
    assert concepts["Bayes theorem"]["tier"] == "core"
    assert "Bayes' theorem" not in concepts
    assert prompts[f"{BAYES}.shortAnswer.2"]["generated_by"] == "postclass"
    # new: tier standard, in the latest unit, explanation kept
    for title in ("Type I error", "p-value"):
        c = concepts[title]
        assert c["tier"] == "standard"
        assert c["status"] == "active"
        assert c["unit_id"] == "mat305.u.unit-3"
        assert c["shipped_in"] == r["batch_id"]
    assert concepts["Type I error"]["confidence_note"] == "Rejecting the null hypothesis when it is true."

    new_ids = {concepts["Type I error"]["concept_id"], concepts["p-value"]["concept_id"]}
    assert plan.new[0].conceptId == BAYES
    assert {n.conceptId for n in plan.new} == {BAYES, *new_ids}
    assert {n.conceptId: n.reason for n in plan.new}[BAYES] == "core"
    assert plan.new[0].promptIds == [f"{BAYES}.shortAnswer.2"]

    shipped = {c.id: c for u in batch.courses[0].units for c in u.concepts}
    assert set(shipped) == {BAYES, *new_ids}
    assert {p.id for p in shipped[BAYES].prompts} == {f"{BAYES}.shortAnswer.1", f"{BAYES}.shortAnswer.2"}
    assert {u.id for u in batch.courses[0].units} == {"mat305.u.unit-2", "mat305.u.unit-3"}
    with db.connect() as conn:
        kinds = dict(conn.execute("SELECT file_id, kind FROM handoff_files;").fetchall())
    assert kinds == {r["batch_id"]: "import", f"postclass-{LECTURE}": "postclass"}


# --- 4. reserve respected; 02:00 takeover sets taken_over -----------------------------------------------------


def _seed_flash_lite_calls(db: Database, n: int) -> None:
    with db.connect() as conn:
        conn.executemany(
            """INSERT INTO model_calls (step, provider, model, pacific_date, started_at, outcome, counts_quota)
               VALUES ('extract', 'gemini', ?, ?, ?, 'ok', 1);""",
            [(FLASH_LITE, pacific_date(NOW), NOW.isoformat())] * n,
        )
        conn.commit()


def test_watch_spends_the_daytime_reserve(env):
    tmp_path, db = env
    archive_class_recording(tmp_path, db)
    _seed_flash_lite_calls(db, 460)  # the nightly passes used everything but the 40-call reserve

    assert get_remaining_quota(db, None, FLASH_LITE, job="pass2", now=NOW) == 0  # pass2 keeps it back
    ctx = make_ctx(db, tmp_path)
    assert ctx.models.remaining(FLASH_LITE) == 40  # watch may spend it
    summary = build.run(ctx, events=events_from(all_events()), launch=lambda cmd: None)
    assert summary.done == 1
    assert row(db)["status"] == "built"


def test_quota_gone_holds_the_lecture_waiting(env):
    tmp_path, db = env
    archive_class_recording(tmp_path, db)
    _seed_flash_lite_calls(db, 500)

    ctx = make_ctx(db, tmp_path)
    summary = build.run(ctx, events=events_from(all_events()), launch=lambda cmd: None)
    assert summary.held == 1 and summary.done == 0
    assert row(db)["status"] == "waiting"
    assert list(to_encore(tmp_path).iterdir()) == []

    with db.connect() as conn:
        conn.execute("DELETE FROM model_calls;")
        conn.commit()
    later = make_ctx(db, tmp_path, now=NOW.replace(minute=45))
    assert build.run(later, events=events_from(all_events()), launch=lambda cmd: None).done == 1
    assert row(db)["status"] == "built"


def test_0200_takeover_sets_taken_over(env):
    tmp_path, db = env
    ctx = make_ctx(db, tmp_path)
    evts = all_events()
    assert build.run(ctx, events=events_from(evts), launch=lambda cmd: None).held == 1  # no transcript yet
    assert row(db)["status"] == "waiting"

    # still waiting at 01:45 → untouched
    before = make_ctx(db, tmp_path, now=datetime(2026, 11, 21, 6, 45, tzinfo=UTC))
    build.run(before, events=events_from(evts), launch=lambda cmd: None)
    assert row(db)["status"] == "waiting"

    assert takeover_due(evts[0].end) == datetime(2026, 11, 21, 7, 0, tzinfo=UTC)
    archive_class_recording(tmp_path, db)  # the transcript lands, but too late for the daytime quiz
    after = make_ctx(db, tmp_path, now=datetime(2026, 11, 21, 7, 15, tzinfo=UTC))
    summary = build.run(after, events=events_from(evts), launch=lambda cmd: None)
    assert row(db)["status"] == "taken_over"
    assert "postclass_quiz" not in after.models.steps()
    assert summary.done == 0
    assert list(to_encore(tmp_path).iterdir()) == []


def test_nightly_takes_over_any_waiting_lecture(env):
    tmp_path, db = env
    build.run(make_ctx(db, tmp_path), events=events_from(all_events()), launch=lambda cmd: None)
    assert row(db)["status"] == "waiting"
    nightly = make_ctx(db, tmp_path, now=datetime(2026, 11, 21, 6, 0, tzinfo=UTC), job="nightly")
    assert build.take_over_waiting(nightly) == 1
    assert row(db)["status"] == "taken_over"
