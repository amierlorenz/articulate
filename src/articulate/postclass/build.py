"""postclass.build: the :15/:45 post-class quiz (§7.1, #71; registry order 140, job `watch`).

1. Classes events that ended in the last 18 h, active course, no row yet (or `waiting`): find the archived class
   window (`source.py`). No ended class → return before touching the archive or any model.
2. One `postclass_quiz` call (Flash Lite, class window only) → 3–5 closed-book items. Every citation's quote is
   checked exactly (case, whitespace and punctuation canonicalised) against the archived segments it cites, inside
   the class window; an item with no passing citation is dropped.
3. The `intake/library` public matcher (embedding shortlist → judge; `watch` spends the daytime reserve) attaches
   each item to an existing concept or makes a new one at `tier: standard`.
4. Write `import-<batchId>.json`, then `postclass-<lectureId>.json` (`kind: postclass`, `importBatch` = that batch),
   then `open -g -b <Encore>`.
5. A lecture still `waiting` at the 02:00 run after its class becomes `taken_over`; the nightly path handles it.
"""
import hashlib
import json
import logging
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from articulate.core.clock import EASTERN
from articulate.core.config import ConfigError, load_config
from articulate.core.handoff_schemas import (
    Concept,
    Course,
    Header,
    ImportBatch,
    Plan,
    PlanNew,
    Prompt,
    SourceFile,
    Unit,
    write_handoff_file,
)
from articulate.core.ids import new_ulid, slugify
from articulate.core.jobs import StepSummary
from articulate.core.models.runner import DefaultModelRunner, Held, ModelRequest, Part
from articulate.core.paths import to_encore_dir
from articulate.core.pipeline import Privacy, RunContext
from articulate.handoff.batch import _parse_concept_details
from articulate.handoff.write import write_import_batch
from articulate.intake.library import match_or_create_concept
from articulate.plan.simple import valid_until
from articulate.postclass.source import (
    LOOKBACK,
    ClassWindow,
    EventLoader,
    Lecture,
    calendar_events,
    ended_lectures,
    find_class_window,
    takeover_due,
)

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parents[3] / "prompts" / "postclass-quiz.md"
_PROMPT_TEXT = PROMPT_PATH.read_text(encoding="utf-8")
PROMPT_VERSION = re.search(r"<!--\s*version:\s*(\S+)\s*-->", _PROMPT_TEXT).group(1)  # type: ignore[union-attr]
PROMPT = re.sub(r"^<!--.*?-->\s*", "", _PROMPT_TEXT, count=1, flags=re.DOTALL)

PRODUCER = "articulate"
MIN_ITEMS, MAX_ITEMS = 3, 5
MAX_MULTIPLE_CHOICE = 1  # mostly free recall
MIN_QUOTE_WORDS = 2
FALLBACK_UNIT = "Lectures"
LEXICAL_KINDS = ("definition", "fact", "method")
PROMPT_KINDS = ("shortAnswer", "flashcard", "multipleChoice")

_CITATION = {
    "type": "object",
    "properties": {
        "startSec": {"type": "number"},
        "endSec": {"type": "number"},
        "quote": {"type": "string"},
    },
    "required": ["startSec", "endSec", "quote"],
}
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "minItems": MIN_ITEMS,
            "maxItems": MAX_ITEMS,
            "items": {
                "type": "object",
                "properties": {
                    "concept": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "kind": {"type": "string", "enum": list(LEXICAL_KINDS)},
                            "explanation": {
                                "type": "object",
                                "properties": {
                                    "text": {"type": "string"},
                                    "citations": {"type": "array", "minItems": 1, "maxItems": 3, "items": _CITATION},
                                },
                                "required": ["text", "citations"],
                            },
                        },
                        "required": ["title", "kind", "explanation"],
                    },
                    "prompt": {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string", "enum": list(PROMPT_KINDS)},
                            "content": {"type": "object"},
                        },
                        "required": ["kind", "content"],
                    },
                },
                "required": ["concept", "prompt"],
            },
        }
    },
    "required": ["items"],
}

_CONTENT_KEYS: dict[str, tuple[str, ...]] = {
    "shortAnswer": ("question", "modelAnswer", "keyPoints"),
    "flashcard": ("front", "back"),
    "multipleChoice": ("question", "options", "correctIndex", "explanation"),
}

Launcher = Callable[[list[str]], Any]


# --- quote check ------------------------------------------------------------------------------------------------

_TYPO = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-"})


def canonical(text: str) -> str:
    """Case, whitespace and punctuation canonicalised: lowercase words joined by single spaces."""
    return " ".join(re.findall(r"[^\W_]+", text.translate(_TYPO).casefold()))


def check_quote(window: ClassWindow, start_sec: float, end_sec: float, quote: str) -> tuple[float, float] | None:
    """The cited segments' bounds when `quote` appears exactly in the class window between the cited times."""
    q = canonical(quote)
    if len(q.split()) < MIN_QUOTE_WORDS:
        return None
    lo, hi = min(start_sec, end_sec), max(start_sec, end_sec)
    segs = [s for s in window.transcript.segments if s[1] > lo - 0.5 and s[0] < hi + 0.5]
    if not segs:
        return None
    if f" {q} " not in f" {canonical(' '.join(s[2] for s in segs))} ":
        return None
    return segs[0][0], segs[-1][1]


# --- the quiz call ----------------------------------------------------------------------------------------------


@dataclass
class QuizItem:
    title: str
    kind: str
    explanation: str
    citations: list[dict[str, Any]]
    prompt_kind: str
    content: dict[str, Any]
    concept_id: str = ""
    is_new: bool = False
    prompt_id: str = ""


@dataclass
class Outcome:
    status: str  # built | waiting | failed
    detail: str = ""
    batch_id: str | None = None
    plan_id: str | None = None
    items: list[QuizItem] = field(default_factory=list)


def transcript_text(window: ClassWindow) -> str:
    return "\n".join(f"[{s:.1f}-{e:.1f}] {t}" for s, e, t in window.transcript.segments)


def _valid_content(kind: str, content: dict[str, Any]) -> dict[str, Any] | None:
    keys = _CONTENT_KEYS.get(kind)
    if keys is None or not all(k in content for k in keys):
        return None
    out = {k: content[k] for k in keys}
    if kind == "multipleChoice":
        opts = out["options"]
        idx = out["correctIndex"]
        if not isinstance(opts, list) or len(opts) < 2 or not isinstance(idx, int) or not 0 <= idx < len(opts):
            return None
    if kind == "shortAnswer" and not isinstance(out["keyPoints"], list):
        return None
    return out


def parse_items(parsed: Any, window: ClassWindow, source_file_id: str) -> list[QuizItem]:
    """Keep items whose citations pass the exact quote check; at most one multiple choice, at most five items."""
    items: list[QuizItem] = []
    mc = 0
    for raw in (parsed or {}).get("items") or []:
        concept = raw.get("concept") or {}
        prompt = raw.get("prompt") or {}
        title = str(concept.get("title") or "").strip()
        kind = concept.get("kind")
        expl = concept.get("explanation") or {}
        p_kind = prompt.get("kind")
        content = _valid_content(p_kind, prompt.get("content") or {}) if isinstance(p_kind, str) else None
        if not title or kind not in LEXICAL_KINDS or content is None:
            logger.info("postclass: dropped malformed item %r", title)
            continue
        if p_kind == "multipleChoice" and mc >= MAX_MULTIPLE_CHOICE:
            logger.info("postclass: dropped extra multiple choice %r", title)
            continue
        citations: list[dict[str, Any]] = []
        for cit in expl.get("citations") or []:
            try:
                bounds = check_quote(window, float(cit["startSec"]), float(cit["endSec"]), str(cit["quote"]))
            except (KeyError, TypeError, ValueError):
                bounds = None
            if bounds is None:
                logger.info("postclass: quote failed the check for %r: %r", title, cit.get("quote"))
                continue
            citations.append(
                {
                    "sourceFileId": source_file_id,
                    "startSec": bounds[0],
                    "endSec": bounds[1],
                    "quote": str(cit["quote"]),
                    "status": "verified",
                    "verifiedBy": "text",
                }
            )
        if not citations:
            continue  # fails closed: nothing in the class backs it
        if p_kind == "multipleChoice":
            mc += 1
        items.append(
            QuizItem(title, kind, str(expl.get("text") or "").strip(), citations[:3], p_kind, content)
        )
        if len(items) == MAX_ITEMS:
            break
    return items


# --- library, prompts, files ------------------------------------------------------------------------------------


def source_file_id(window: ClassWindow) -> str:
    digest = hashlib.sha256(f"pocket:{window.recording_id}".encode()).hexdigest()
    return f"f.{digest[:16]}"


def _record_source_file(ctx: RunContext, lecture: Lecture, window: ClassWindow, file_id: str) -> SourceFile:
    name = f"{lecture.event.summary or lecture.course_key} {lecture.lecture_id.rsplit('.', 1)[-1]} (recording)"
    content_hash = hashlib.sha256(transcript_text(window).encode()).hexdigest()
    with ctx.db.connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO source_files (file_id, course_key, origin, slot, display_name, role, created_at)
            VALUES (?, ?, 'pocket', ?, ?, 'transcript', ?);
            """,
            (file_id, lecture.course_key, f"pocket:{window.recording_id}", name, ctx.now.isoformat()),
        )
        conn.commit()
    return SourceFile(id=file_id, origin="pocket", displayName=name, contentHash=content_hash)


def _fallback_unit(ctx: RunContext, course_key: str) -> str:
    """Where a new post-class concept lives until 02:00 re-tiers it: the course's latest unit, else `Lectures`."""
    with ctx.db.connect() as conn:
        row = conn.execute(
            "SELECT unit_id FROM units WHERE course_key = ? ORDER BY position DESC LIMIT 1;", (course_key,)
        ).fetchone()
        if row:
            return row["unit_id"]
        unit_id = f"{course_key}.u.{slugify(FALLBACK_UNIT)}"
        conn.execute(
            "INSERT OR IGNORE INTO units (unit_id, course_key, name, position) VALUES (?, ?, ?, 999);",
            (unit_id, course_key, FALLBACK_UNIT),
        )
        conn.commit()
        return unit_id


def _next_prompt_id(conn: Any, concept_id: str, kind: str) -> str:
    rows = conn.execute("SELECT prompt_id FROM prompts WHERE concept_id = ?;", (concept_id,)).fetchall()
    prefix = f"{concept_id}.{kind}."
    nums = [int(r["prompt_id"][len(prefix):]) for r in rows if r["prompt_id"].startswith(prefix)
            and r["prompt_id"][len(prefix):].isdigit()]
    return f"{prefix}{max(nums, default=0) + 1}"


def attach_to_library(ctx: RunContext, lecture: Lecture, items: list[QuizItem]) -> None:
    """Match each item to the library (or make a new standard concept) and store its prompt."""
    now = ctx.now.isoformat()
    for it in items:
        payload = {"title": it.title, "explanation": it.explanation, "kind": it.kind, "citations": it.citations}
        it.concept_id, it.is_new = match_or_create_concept(payload, lecture.course_key, ctx, reserve=True)
        with ctx.db.connect() as conn:
            if it.is_new:
                conn.execute(
                    """
                    UPDATE concepts SET unit_id = COALESCE(unit_id, ?), confidence_note = COALESCE(confidence_note, ?),
                           updated_at = ?
                    WHERE concept_id = ?;
                    """,
                    (_fallback_unit(ctx, lecture.course_key), it.explanation or it.title, now, it.concept_id),
                )
            it.prompt_id = _next_prompt_id(conn, it.concept_id, it.prompt_kind)
            conn.execute(
                """
                INSERT INTO prompts (prompt_id, concept_id, kind, content_json, status, generated_by,
                                     prompt_version, created_at)
                VALUES (?, ?, ?, ?, 'active', 'postclass', ?, ?);
                """,
                (it.prompt_id, it.concept_id, it.prompt_kind, json.dumps(it.content), PROMPT_VERSION, now),
            )
            conn.commit()


def build_batch(ctx: RunContext, lecture: Lecture, items: list[QuizItem], source: SourceFile) -> ImportBatch:
    """A delta batch with just this lecture's concepts (each with all its prompts) and the recording."""
    concept_ids = list(dict.fromkeys(it.concept_id for it in items))
    with ctx.db.connect() as conn:
        course_row = conn.execute("SELECT * FROM courses WHERE course_key = ?;", (lecture.course_key,)).fetchone()
        units: dict[str, Unit] = {}
        for cid in concept_ids:
            c = conn.execute("SELECT * FROM concepts WHERE concept_id = ?;", (cid,)).fetchone()
            if c is None:
                continue
            unit_id = c["unit_id"] or _fallback_unit(ctx, lecture.course_key)
            if unit_id not in units:
                u = conn.execute("SELECT * FROM units WHERE unit_id = ?;", (unit_id,)).fetchone()
                units[unit_id] = Unit(
                    id=unit_id,
                    name=(u["name"] if u and u["name"] else unit_id),
                    order=(u["position"] if u and u["position"] is not None else 1),
                )
            prompts = [
                Prompt(
                    id=p["prompt_id"],
                    kind=p["kind"],
                    status=p["status"] if p["status"] in ("active", "retired") else "active",
                    content=json.loads(p["content_json"] or "{}"),
                )
                for p in conn.execute(
                    "SELECT * FROM prompts WHERE concept_id = ? AND status IN ('active', 'retired') ORDER BY prompt_id;",
                    (cid,),
                )
            ]
            units[unit_id].concepts.append(
                Concept(
                    id=cid,
                    title=c["title"] or cid,
                    explanation=c["confidence_note"] or c["title"] or cid,
                    kind=c["kind"] if c["kind"] in LEXICAL_KINDS else "definition",
                    status="retired" if c["status"] == "retired" else "active",
                    tier=c["tier"] if c["tier"] in ("core", "standard", "background", "out") else "standard",
                    details=_parse_concept_details(c["details_json"]),
                    prompts=prompts,
                )
            )

    course = Course(
        id=lecture.course_key,
        name=(course_row["name"] if course_row and course_row["name"] else lecture.course_key.upper()),
        shortName=lecture.course_key.upper(),
        coding=bool(course_row["coding"]) if course_row else False,
        units=sorted(units.values(), key=lambda u: u.order),
        sourceFiles=[source],
    )
    return ImportBatch(
        header=Header(schema="encore.import", version=2, id=new_ulid(), created_at=ctx.now, producer=PRODUCER),
        courses=[course],
    )


def build_plan(ctx: RunContext, lecture: Lecture, items: list[QuizItem], batch_id: str) -> Plan:
    new: list[PlanNew] = []
    with ctx.db.connect() as conn:
        for it in items:
            row = conn.execute("SELECT tier FROM concepts WHERE concept_id = ?;", (it.concept_id,)).fetchone()
            tier = row["tier"] if row and row["tier"] in ("core", "standard", "background") else "standard"
            new.append(PlanNew(conceptId=it.concept_id, promptIds=[it.prompt_id], reason=tier))
    return Plan(
        header=Header(
            schema="encore.plan",
            version=1,
            id=f"postclass-{lecture.lecture_id}",
            created_at=ctx.now,
            producer=PRODUCER,
        ),
        kind="postclass",
        date=lecture.event.start.astimezone(EASTERN).date(),
        validUntil=valid_until(ctx.now, EASTERN),
        reviewScope=[lecture.course_key],
        new=new,
        budgetMinutes={"target": 5, "planned": len(new)},
        kindsMix={"freeRecallMin": 0.5},
        importBatch=batch_id,
    )


def _bundle_id(ctx: RunContext) -> str:
    if ctx.config is not None and getattr(ctx.config, "articulate", None) is not None:
        return ctx.config.articulate.encore.bundle_id or "com.amierlorenz.Encore"
    return "com.amierlorenz.Encore"


def _open_encore(cmd: list[str]) -> Any:
    return subprocess.run(cmd, check=False, capture_output=True, text=True)


def write_handoff(
    ctx: RunContext, lecture: Lecture, items: list[QuizItem], source: SourceFile, launch: Launcher
) -> tuple[str, str]:
    """import batch first, then the post-class plan that names it, then wake Encore."""
    batch = build_batch(ctx, lecture, items, source)
    write_import_batch(batch)
    batch_id = batch.header.id
    now = ctx.now.isoformat()
    shipped = [(c.id, [p.id for p in c.prompts]) for u in batch.courses[0].units for c in u.concepts]
    with ctx.db.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO handoff_files (file_id, kind, written_at) VALUES (?, 'import', ?);",
            (batch_id, now),
        )
        for cid, pids in shipped:
            conn.execute("UPDATE concepts SET shipped_in = ? WHERE concept_id = ?;", (batch_id, cid))
            for pid in pids:
                conn.execute(
                    "UPDATE prompts SET shipped_in = COALESCE(shipped_in, ?) WHERE prompt_id = ?;", (batch_id, pid)
                )
        conn.commit()

    plan = build_plan(ctx, lecture, items, batch_id)
    write_handoff_file(to_encore_dir() / f"postclass-{lecture.lecture_id}.json", plan)
    with ctx.db.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO handoff_files (file_id, kind, written_at) VALUES (?, 'postclass', ?);",
            (plan.header.id, now),
        )
        conn.commit()

    try:
        launch(["open", "-g", "-b", _bundle_id(ctx)])
    except Exception as e:  # noqa: BLE001
        logger.warning("postclass: could not open Encore: %s", e)
    return batch_id, plan.header.id


def build_quiz(ctx: RunContext, lecture: Lecture, window: ClassWindow, launch: Launcher = _open_encore) -> Outcome:
    if ctx.models is None:
        return Outcome("waiting", "no model runner")
    req = ModelRequest(
        step="postclass_quiz",
        material=Privacy.CLASS,
        parts=[Part(text=PROMPT), Part(text=f"Course: {lecture.course_key}\n\nTranscript:\n{transcript_text(window)}")],
        json_schema=SCHEMA,
        item_id=lecture.lecture_id,
    )
    try:
        res = ctx.models.run(req)
    except Held as h:
        return Outcome("waiting", f"held: {h.reason}")
    file_id = source_file_id(window)
    items = parse_items(res.parsed, window, file_id)
    if not items:
        return Outcome("failed", "no item passed the quote check")
    source = _record_source_file(ctx, lecture, window, file_id)
    attach_to_library(ctx, lecture, items)
    batch_id, plan_id = write_handoff(ctx, lecture, items, source, launch)
    return Outcome("built", f"{len(items)} items", batch_id, plan_id, items)


# --- the step ---------------------------------------------------------------------------------------------------


def take_over_waiting(ctx: RunContext) -> int:
    """Lectures still `waiting` once their 02:00 run has come go to the nightly path (`taken_over`)."""
    with ctx.db.connect() as conn:
        rows = conn.execute(
            "SELECT lecture_id, class_end FROM postclass_quizzes WHERE status = 'waiting';"
        ).fetchall()
        taken = 0
        for r in rows:
            end = datetime.fromisoformat(r["class_end"]) if r["class_end"] else None
            if ctx.job == "nightly" or end is None or ctx.now >= takeover_due(end):
                conn.execute("UPDATE postclass_quizzes SET status = 'taken_over' WHERE lecture_id = ?;",
                             (r["lecture_id"],))
                taken += 1
                stale = to_encore_dir() / f"postclass-{r['lecture_id']}.json"
                stale.unlink(missing_ok=True)
        conn.commit()
    return taken


def _upsert_row(ctx: RunContext, lecture: Lecture, status: str, recording_id: str | None = None,
                outcome: Outcome | None = None) -> None:
    now = ctx.now.isoformat()
    with ctx.db.connect() as conn:
        conn.execute(
            """
            INSERT INTO postclass_quizzes (lecture_id, course_key, event_id, recording_id, status, batch_id, plan_id,
                                           created_at, built_at, class_end)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(lecture_id) DO UPDATE SET
              status = excluded.status,
              recording_id = COALESCE(excluded.recording_id, recording_id),
              batch_id = COALESCE(excluded.batch_id, batch_id),
              plan_id = COALESCE(excluded.plan_id, plan_id),
              built_at = COALESCE(excluded.built_at, built_at);
            """,
            (
                lecture.lecture_id,
                lecture.course_key,
                lecture.event.id,
                recording_id,
                status,
                outcome.batch_id if outcome else None,
                outcome.plan_id if outcome else None,
                now,
                now if status == "built" else None,
                lecture.class_end.isoformat(),
            ),
        )
        conn.commit()


def run(ctx: RunContext, events: EventLoader = calendar_events, launch: Launcher = _open_encore) -> StepSummary:
    summary = StepSummary()
    taken = take_over_waiting(ctx)

    try:
        evts = events(ctx, ctx.now - LOOKBACK, ctx.now)
    except Exception as e:  # noqa: BLE001
        logger.warning("postclass: calendar read failed: %s", e)
        return StepSummary(failed=1, detail=f"calendar: {e}")

    with ctx.db.connect() as conn:
        status = {r["lecture_id"]: r["status"] for r in conn.execute("SELECT lecture_id, status FROM postclass_quizzes;")}
    lectures = [lec for lec in ended_lectures(ctx, evts) if status.get(lec.lecture_id, "waiting") == "waiting"]
    if not lectures:
        summary.detail = f"no ended class waiting (taken over {taken})"
        return summary

    for lec in lectures:
        if ctx.now >= takeover_due(lec.class_end):
            _upsert_row(ctx, lec, "taken_over")
            continue
        window = find_class_window(ctx, lec)
        if window is None:
            _upsert_row(ctx, lec, "waiting")
            summary.held += 1
            continue
        try:
            outcome = build_quiz(ctx, lec, window, launch)
        except Exception as e:  # noqa: BLE001
            logger.error("postclass: %s failed: %s", lec.lecture_id, e)
            outcome = Outcome("failed", str(e))
        _upsert_row(ctx, lec, outcome.status, window.recording_id, outcome)
        if outcome.status == "built":
            summary.done += 1
        elif outcome.status == "waiting":
            summary.held += 1
        else:
            summary.failed += 1
    summary.detail = f"{summary.done} built, {summary.held} waiting, {summary.failed} failed, {taken} taken over"
    return summary


def _ensure_services(ctx: RunContext) -> None:
    """Fill what `run_job` leaves empty; a test or an earlier step may have set them already."""
    if ctx.config is None:
        try:
            ctx.config = load_config(db=ctx.db)
        except ConfigError as e:
            logger.warning("postclass: config not loaded: %s", e)
    if ctx.models is None:
        ctx.models = DefaultModelRunner(ctx.db, ctx.config, job=ctx.job, run_id=ctx.run_id)


def step(ctx: RunContext) -> StepSummary:
    """Registry entry `postclass.build` (order 140, job `watch`)."""
    _ensure_services(ctx)
    return run(ctx)
