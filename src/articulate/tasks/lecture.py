"""Task extraction from class lecture recordings on the local model (§7.1, Issue #75, SPEC-requirements 3.23).

At 02:00 (the `nightly` job), class windows of lectures go through `prompts/task-lecture.md` on `qwen3:8b`:
- Instructor directives with a concrete action become tasks (high band, pending).
- Directives that match a Canvas assignment for the same course due within ±2 days are dropped (master-todo owns Canvas).
- Date changes ("quiz moved to Friday") become weekly flag lines in `Inbox/Intake flags.md`, not tasks.
- Instructor commitments and musings are dropped.
"""

import json
import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from articulate.core.clock import EASTERN
from articulate.core.db import Database
from articulate.core.jobs import StepSummary
from articulate.core.models.runner import Held, ModelRequest, Part
from articulate.core.pipeline import Privacy, RunContext
from articulate.core.taskqueue import TaskQueue, TaskRequest
from articulate.intake.canvas import CanvasClient
from articulate.tasks.extract import (
    DEDUPE_COSINE,
    Recording,
    Segment,
    _cosine,
    _log_drop,
    _norm,
    _note_link,
    _recent_open_vectors,
    _set_state,
    cap_title,
    find_quote,
    load_recording,
    resolve_due,
    store_embedding,
    transcript_chunks,
)

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parents[3] / "prompts" / "task-lecture.md"
_PROMPT_TEXT = PROMPT_PATH.read_text(encoding="utf-8")
PROMPT_VERSION = re.search(r"<!--\s*version:\s*(\S+)\s*-->", _PROMPT_TEXT).group(1)  # type: ignore[union-attr]
PROMPT = re.sub(r"^<!--.*?-->\s*", "", _PROMPT_TEXT, count=1, flags=re.DOTALL)

DATE_CHANGE_FLAG_KIND = "tasks.date_change"
DATE_CHANGE_FLAG_CHOICES = [("keep", "keep"), ("dismiss", "dismiss")]

KINDS = ["directive", "date_change", "instructor_commitment", "musing"]
NEVER_TASKS = {"instructor_commitment", "musing"}
CONFIDENCES = ["high", "medium", "low"]

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "quote": {"type": "string"},
                    "kind": {"type": "string", "enum": KINDS},
                    "confidence": {"type": "string", "enum": CONFIDENCES},
                    "due_spoken": {"type": ["string", "null"]},
                },
                "required": ["title", "quote", "kind", "confidence", "due_spoken"],
            },
        }
    },
    "required": ["tasks"],
}


def _normalize_text(s: str) -> str:
    s = s.lower().translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'}))
    return re.sub(r"\s+", " ", s).strip()


def matches_canvas_assignment(
    title: str,
    quote: str,
    due_at: str | None,
    rec_date: date,
    assignments: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """
    Check if a directive looks like a Canvas assignment for the same course
    due within ±2 days (§7.1, Issue #20).
    """
    directive_date = datetime.fromisoformat(due_at).date() if due_at else rec_date
    norm_title = _normalize_text(title)
    norm_quote = _normalize_text(quote)

    for asgn in assignments:
        asgn_name = asgn.get("name", "")
        norm_asgn = _normalize_text(asgn_name)
        if not norm_asgn or len(norm_asgn) < 3:
            continue

        name_match = (norm_asgn in norm_title) or (norm_asgn in norm_quote)
        if not name_match:
            tokens = [
                t
                for t in re.findall(r"\w+", norm_asgn)
                if t not in ("assignment", "submission", "due")
            ]
            if tokens and all(t in norm_title or t in norm_quote for t in tokens):
                name_match = True

        if not name_match:
            continue

        raw_asgn_due = asgn.get("due_at")
        if not raw_asgn_due:
            continue
        try:
            clean_due = str(raw_asgn_due).replace("Z", "+00:00")
            asgn_dt = datetime.fromisoformat(clean_due).astimezone(EASTERN).date()
        except (ValueError, TypeError):
            continue

        if abs((asgn_dt - directive_date).days) <= 2:
            return asgn

    return None


def _get_course_assignments(
    course_key: str | None,
    ctx: RunContext,
    client: Any = None,
) -> list[dict[str, Any]]:
    if not course_key:
        return []

    canvas_id: int | None = None
    if ctx.config and ctx.config.courses and course_key in ctx.config.courses.courses:
        canvas_id = ctx.config.courses.courses[course_key].canvas_id
    elif client is not None and hasattr(client, "course_canvas_ids"):
        canvas_id = client.course_canvas_ids.get(course_key)
    elif client is not None and hasattr(client, "list_assignments"):
        canvas_id = getattr(client, "default_canvas_id", 1)

    if canvas_id is None:
        return []

    try:
        canvas_client = client or CanvasClient()
        return canvas_client.list_assignments(canvas_id)
    except Exception as e:  # noqa: BLE001
        logger.info(
            "tasks: could not list Canvas assignments for %s (%s): %s", course_key, canvas_id, e
        )
        return []


def _eligible_items(db: Database) -> list[dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT i.* FROM items i LEFT JOIN task_extractions e ON e.item_id = i.item_id
               WHERE i.source = 'pocket' AND i.kind IN ('recording', 'span')
                 AND i.privacy = 'class' AND i.status != 'skipped' AND e.item_id IS NULL
               ORDER BY i.created_at, i.item_id;"""
        ).fetchall()
    return [dict(r) for r in rows]


def _candidates(ctx: RunContext, item_id: str, rec: Recording) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for chunk in transcript_chunks(rec.segments):
        res = ctx.models.run(
            ModelRequest(
                step="personal_text",
                material=Privacy.CLASS,
                item_id=item_id,
                json_schema=SCHEMA,
                parts=[Part(text=PROMPT), Part(text=f"Lecture: {rec.title}\n\n{chunk}")],
            )
        )
        parsed = res.parsed if res.parsed is not None else json.loads(res.text or "{}")
        out.extend(c for c in (parsed or {}).get("tasks") or [] if isinstance(c, dict))
    return out


def _extract_lecture_item(
    ctx: RunContext,
    item: dict[str, Any],
    summary: StepSummary,
    client: Any = None,
) -> None:
    item_id = item["item_id"]
    payload = json.loads(item["payload_json"] or "{}")
    if payload.get("merged"):
        return

    rec = load_recording(item, payload)
    if rec is None:
        logger.info("tasks: no archived transcript yet for %s", item_id)
        return

    course_key = item.get("course_key") or payload.get("course_key")
    assignments = _get_course_assignments(course_key, ctx, client=client)

    try:
        cands = _candidates(ctx, item_id, rec) if rec.segments else []
        keep: list[tuple[dict[str, Any], Segment]] = []
        for c in cands:
            kind, conf = c.get("kind"), c.get("confidence")
            seg = find_quote(str(c.get("quote") or ""), rec.segments)
            if (
                not str(c.get("title") or "").strip()
                or kind not in KINDS
                or conf not in CONFIDENCES
            ):
                _log_drop(ctx.db, item_id, c, "invalid", ctx.now)
            elif kind in NEVER_TASKS:
                _log_drop(ctx.db, item_id, c, kind, ctx.now)
            elif seg is None:
                _log_drop(ctx.db, item_id, c, "quote_not_found", ctx.now)
            else:
                keep.append((c, seg))

        to_embed = [
            cap_title(c["title"])
            for c, _ in keep
            if c["kind"] == "directive" and c["confidence"] != "low"
        ]
        vectors: list[list[float]] = []
        if to_embed:
            emb = ctx.models.run(
                ModelRequest(
                    step="embed_personal",
                    material=Privacy.CLASS,
                    item_id=item_id,
                    embed=True,
                    parts=[Part(text=t) for t in to_embed],
                )
            )
            vectors = list(emb.vectors or [])
    except Held as h:
        logger.info("tasks: lecture extraction for %s held (%s)", item_id, h.reason)
        summary.held += 1
        return

    queue = ctx.tasks or TaskQueue(ctx.db)
    link = _note_link(ctx, item_id)
    rec_date_dt = rec.recorded_at.astimezone(EASTERN).date()
    rec_date = rec_date_dt.isoformat()
    recent = _recent_open_vectors(ctx.db, ctx.now)

    embedded_directives = [
        i for i, (c, _) in enumerate(keep) if c["kind"] == "directive" and c["confidence"] != "low"
    ]
    vec_for = dict(zip(embedded_directives, vectors, strict=False))

    for idx, (c, seg) in enumerate(keep):
        start = int(seg.start)
        ext = f"task:{rec.recording_id}:{start}"
        title = cap_title(c["title"])
        quote = " ".join(str(c["quote"]).split())
        spoken = c.get("due_spoken")
        if spoken and _norm(spoken) not in _norm(quote):
            spoken = None
        due_at, has_time = resolve_due(spoken, rec.recorded_at)

        if c["kind"] == "date_change":
            # Date change ("quiz moved to Friday") becomes a weekly flag line, not a task (§7.1)
            if ctx.flags is not None:
                ctx.flags.add(
                    DATE_CHANGE_FLAG_KIND,
                    f"Date change: {title} — “{quote}” ({rec.title}, {rec_date})",
                    DATE_CHANGE_FLAG_CHOICES,
                    item_id=ext,
                )
            continue

        if c["kind"] == "directive":
            matched_asgn = matches_canvas_assignment(title, quote, due_at, rec_date_dt, assignments)
            if not queue.enqueue(
                TaskRequest(
                    external_id=ext,
                    title=title,
                    due_at=due_at,
                    has_due_time=has_time,
                    notes=f"“{quote}”\n{rec.title} · {rec_date} · @{start // 60:02d}:{start % 60:02d}",
                    link=link,
                    origin_item_id=item_id,
                    band="high",
                    prompt_version=PROMPT_VERSION,
                )
            ):
                continue

            if matched_asgn is not None:
                # Same-course assignment due within ±2 days -> dropped (§7.1, Issue #20)
                _set_state(ctx.db, ext, "dropped")
                _log_drop(ctx.db, item_id, c, "canvas_assignment", ctx.now)
                if ctx.runlog:
                    ts = ctx.now.astimezone(EASTERN).strftime("%Y-%m-%d %H:%M")
                    ctx.runlog.line(
                        f"- {ts} tasks: dropped Canvas assignment look-alike {title} "
                        f"({matched_asgn.get('name')}) in {course_key or 'unknown'}"
                    )
                continue

            vec = vec_for.get(idx)
            dup = None
            if vec:
                dup = next((other for other, v in recent if _cosine(vec, v) >= DEDUPE_COSINE), None)
                store_embedding(ctx.db, ext, vec)
            if dup:
                _set_state(ctx.db, ext, "dropped")
                if ctx.runlog:
                    ts = ctx.now.astimezone(EASTERN).strftime("%Y-%m-%d %H:%M")
                    ctx.runlog.line(f"- {ts} tasks: dropped near-duplicate {ext} of {dup}")
                continue

            if vec:
                recent.append((ext, vec))

            summary.done += 1

    with ctx.db.connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO task_extractions
               (item_id, recording_id, prompt_version, extracted_at, candidates) VALUES (?, ?, ?, ?, ?);""",
            (item_id, rec.recording_id, PROMPT_VERSION, ctx.now.isoformat(), len(cands)),
        )
        conn.commit()


def extract_lectures(ctx: RunContext, client: Any = None) -> StepSummary:
    """Extract tasks from finished class lecture recordings/spans."""
    summary = StepSummary()
    for item in _eligible_items(ctx.db):
        _extract_lecture_item(ctx, item, summary, client=client)
        if summary.held:
            break
    return summary


def resolve_date_change_flag(flag_id: str, chosen: str, ctx: RunContext) -> None:
    """Resolver registered for `tasks.date_change`: dismissed or kept."""
    # When resolved, flag status is updated by core.flags; no tasks to send
