"""plan.daily step: inputs from SQLite and state.json, written to to-encore/plan.json (#66, 2.39; Phase 3 #70)."""
import json
import logging
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from articulate.core.clock import EASTERN
from articulate.core.handoff_schemas import State, write_handoff_file
from articulate.core.ids import new_ulid
from articulate.core.jobs import StepSummary
from articulate.core.paths import to_encore_dir
from articulate.core.pipeline import RunContext
from articulate.core.taskqueue import TaskQueue, TaskRequest
from articulate.plan.backlog import idle_streak_start, idle_task_id, session_days
from articulate.plan.contrasts import ContrastPrompt
from articulate.plan.daily import Correction, PlanInputs, build_daily_plan
from articulate.plan.ramp import is_boosted
from articulate.plan.simple import Candidate, ExamDate, introduced_ids

logger = logging.getLogger(__name__)

NO_STATE_FLAG = "encore_no_state"
PRODUCER = "articulate"


def _parse_exam_date(val: str | None) -> date | None:
    if not val:
        return None
    d_str = val[:10]
    try:
        return date.fromisoformat(d_str)
    except ValueError:
        return None


def step(ctx: RunContext) -> StepSummary:
    """Generate daily plan and write to to-encore/plan.json."""
    summary = StepSummary()

    # 1. Determine active courses
    if ctx.config and ctx.config.courses and ctx.config.courses.courses:
        active_courses = sorted([k for k, c in ctx.config.courses.courses.items() if c.active])
    else:
        with ctx.db.connect() as conn:
            rows = conn.execute("SELECT course_key FROM courses WHERE active = 1;").fetchall()
            active_courses = sorted([r["course_key"] for r in rows])

    # 2. Read state snapshot
    state: State | None = None
    with ctx.db.connect() as conn:
        row = conn.execute(
            "SELECT body_json FROM encore_snapshots WHERE schema = 'encore.state' ORDER BY exported_at DESC LIMIT 1;"
        ).fetchone()
        if row and row["body_json"]:
            try:
                state = State.model_validate_json(row["body_json"])
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to parse encore.state snapshot: %s", e)
                state = None

    if state is None:
        introduced = introduced_ids(None)
        if ctx.flags:
            with ctx.db.connect() as conn:
                existing = conn.execute(
                    "SELECT 1 FROM flags WHERE kind = ? AND status = 'open' LIMIT 1;",
                    (NO_STATE_FLAG,),
                ).fetchone()
            if not existing:
                ctx.flags.add(
                    NO_STATE_FLAG,
                    "No Encore state snapshot found; assuming nothing introduced yet",
                    [("ok", "Acknowledge")],
                )
    else:
        introduced = introduced_ids(state)

    # 3. Exams (§3.14), then candidates (active concepts with at least one active prompt)
    with ctx.db.connect() as conn:
        exam_rows = conn.execute("SELECT exam_id, course_key, name, kind, date, unit_ids_json FROM exams;").fetchall()
    exams = [
        ExamDate(
            exam_id=r["exam_id"],
            course_key=r["course_key"],
            date=_parse_exam_date(r["date"]),
            units=_unit_list(r["exam_id"], r["unit_ids_json"]),
            kind=r["kind"],
            name=r["name"],
        )
        for r in exam_rows
    ]

    with ctx.db.connect() as conn:
        cand_rows = conn.execute(
            """
            SELECT c.concept_id, c.course_key, c.unit_id, u.name AS unit_name, c.tier, c.created_at,
                   c.tier_reasons_json
            FROM concepts c
            LEFT JOIN units u ON c.unit_id = u.unit_id
            WHERE c.status = 'active'
              AND EXISTS (
                  SELECT 1 FROM prompts p
                  WHERE p.concept_id = c.concept_id AND p.status = 'active' AND p.kind != 'contrast'
              )
            ORDER BY c.created_at, c.concept_id;
            """
        ).fetchall()

    candidates = []
    for r in cand_rows:
        cand = Candidate(
            concept_id=r["concept_id"],
            course_key=r["course_key"],
            unit_id=r["unit_id"],
            unit_name=r["unit_name"],
            tier=r["tier"],
            created_at=r["created_at"],
        )
        if is_boosted(cand, r["tier_reasons_json"], exams):
            cand = replace(cand, boosted=True)
        candidates.append(cand)

    # 4. Attempts ledger, corrections, skipped post-class quizzes, contrast prompts
    attempts = load_attempts(ctx)
    known = {p.promptId for p in state.prompts if p.introducedAt is not None} if state else set()
    corrections = load_corrections(ctx, introduced, known)
    postclass_skipped = load_postclass_skipped(ctx, introduced, attempts)
    contrasts = load_contrasts(ctx)

    # 5. Build plan
    plan_id = new_ulid()
    plan = build_daily_plan(
        PlanInputs(
            candidates=candidates,
            exams=exams,
            introduced=introduced,
            active_courses=active_courses,
            now=ctx.now,
            tz=EASTERN,
            plan_id=plan_id,
            producer=PRODUCER,
            state=state,
            attempts=attempts,
            corrections=corrections,
            postclass_skipped=postclass_skipped,
            contrasts=contrasts,
        )
    )

    # 6. Write handoff plan.json
    out_path = to_encore_dir() / "plan.json"
    write_handoff_file(out_path, plan)

    # 7. Record in handoff_files table
    now_str = ctx.now.isoformat()
    with ctx.db.connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO handoff_files (file_id, kind, written_at)
            VALUES (?, 'plan', ?);
            """,
            (plan_id, now_str),
        )
        conn.commit()

    # 8. Three days in a row with no session: one study:idle task per streak (§7.1 rule 4)
    start = idle_streak_start(session_days([t for _, t in attempts], EASTERN), plan.date)
    if start is not None:
        TaskQueue(ctx.db).enqueue(TaskRequest(
            external_id=idle_task_id(start),
            title=f"Study: no Encore session since {(start - timedelta(days=1)).isoformat()}",
            notes="Three days in a row without a study session. Today's plan limits reviews to what fits.",
            band="system",
        ))

    summary.done += 1
    return summary


def _unit_list(exam_id: str, raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except ValueError as e:
        logger.debug("Failed to parse unit_ids_json for exam %s: %s", exam_id, e)
        return []
    return [str(x) for x in parsed] if isinstance(parsed, list) else []


def _parse_ts(val: str) -> datetime:
    dt = datetime.fromisoformat(val)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _table_exists(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?;", (name,)).fetchone() is not None


def load_attempts(ctx: RunContext) -> list[tuple[str, datetime]]:
    """(concept_id, answered_at) from the attempts ledger. Read locally only; never leaves the Mac."""
    with ctx.db.connect() as conn:
        if not _table_exists(conn, "attempts"):
            return []
        rows = conn.execute("SELECT concept_id, answered_at FROM attempts;").fetchall()
    out = []
    for r in rows:
        try:
            out.append((r["concept_id"], _parse_ts(r["answered_at"])))
        except ValueError:
            logger.debug("Skipping attempt with bad answered_at %r", r["answered_at"])
    return out


def load_corrections(ctx: RunContext, introduced: set[str], known: set[str]) -> list[Correction]:
    """Active prompts Encore hasn't seen, on an introduced concept that has a retired prompt of the same kind.

    That is what re-intake leaves behind when the expected answer changed (#23: retire the old prompt, new id).
    """
    with ctx.db.connect() as conn:
        rows = conn.execute(
            """
            SELECT p.concept_id, p.prompt_id
            FROM prompts p
            WHERE p.status = 'active' AND p.kind != 'contrast'
              AND EXISTS (
                  SELECT 1 FROM prompts old
                  WHERE old.concept_id = p.concept_id AND old.kind = p.kind
                    AND old.status = 'retired' AND old.prompt_id != p.prompt_id
              )
            ORDER BY p.created_at, p.prompt_id;
            """
        ).fetchall()
    return [
        Correction(r["concept_id"], r["prompt_id"])
        for r in rows
        if r["concept_id"] in introduced and r["prompt_id"] not in known
    ]


def load_postclass_skipped(
    ctx: RunContext, introduced: set[str], attempts: list[tuple[str, datetime]]
) -> list[str]:
    """Concepts of built post-class quizzes from before today with no post-class answers and not introduced."""
    today = ctx.now.astimezone(EASTERN).date().isoformat()
    answered = {cid for cid, _ in attempts}
    with ctx.db.connect() as conn:
        if not _table_exists(conn, "postclass_quizzes"):
            return []
        rows = conn.execute(
            """
            SELECT p.concept_id
            FROM postclass_quizzes q
            JOIN prompts p ON p.shipped_in = q.batch_id
            WHERE q.status = 'built' AND q.batch_id IS NOT NULL AND substr(q.lecture_id, -10) < ?
            ORDER BY q.lecture_id, p.created_at, p.prompt_id;
            """,
            (today,),
        ).fetchall()
    out: list[str] = []
    for r in rows:
        cid = r["concept_id"]
        if cid not in introduced and cid not in answered and cid not in out:
            out.append(cid)
    return out


def load_contrasts(ctx: RunContext) -> list[ContrastPrompt]:
    with ctx.db.connect() as conn:
        rows = conn.execute(
            """
            SELECT p.prompt_id, p.content_json, g.status AS group_status
            FROM prompts p
            LEFT JOIN contrast_groups g ON g.group_id = json_extract(p.content_json, '$.groupId')
            WHERE p.kind = 'contrast' AND p.status = 'active'
            ORDER BY p.prompt_id;
            """
        ).fetchall()
    out = []
    for r in rows:
        if r["group_status"] in ("dormant", "suspended"):
            continue
        try:
            content = json.loads(r["content_json"] or "{}")
            a, b = sorted(str(x) for x in content["conceptIds"])
        except (ValueError, KeyError, TypeError):
            logger.debug("Skipping contrast prompt %s with bad content", r["prompt_id"])
            continue
        out.append(ContrastPrompt(r["prompt_id"], (a, b), content.get("groupId")))
    return out
