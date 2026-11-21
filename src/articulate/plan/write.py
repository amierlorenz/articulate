"""plan.daily step: inputs from SQLite and state.json, written to to-encore/plan.json (#66, 2.39)."""
import json
import logging
from datetime import date

from articulate.core.clock import EASTERN
from articulate.core.handoff_schemas import State, write_handoff_file
from articulate.core.ids import new_ulid
from articulate.core.jobs import StepSummary
from articulate.core.paths import to_encore_dir
from articulate.core.pipeline import RunContext
from articulate.plan.simple import (
    Candidate,
    ExamDate,
    build_plan,
    introduced_ids,
)

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

    # 3. Query candidates (active concepts with at least one active prompt)
    with ctx.db.connect() as conn:
        cand_rows = conn.execute(
            """
            SELECT c.concept_id, c.course_key, c.unit_id, u.name AS unit_name, c.tier, c.created_at
            FROM concepts c
            LEFT JOIN units u ON c.unit_id = u.unit_id
            WHERE c.status = 'active'
              AND EXISTS (
                  SELECT 1 FROM prompts p
                  WHERE p.concept_id = c.concept_id AND p.status = 'active'
              )
            ORDER BY c.created_at, c.concept_id;
            """
        ).fetchall()

    candidates = [
        Candidate(
            concept_id=r["concept_id"],
            course_key=r["course_key"],
            unit_id=r["unit_id"],
            unit_name=r["unit_name"],
            tier=r["tier"],
            created_at=r["created_at"],
        )
        for r in cand_rows
    ]

    # 4. Query exams
    with ctx.db.connect() as conn:
        exam_rows = conn.execute(
            "SELECT exam_id, course_key, date, unit_ids_json FROM exams;"
        ).fetchall()

    exams: list[ExamDate] = []
    for r in exam_rows:
        unit_ids: list[str] = []
        if r["unit_ids_json"]:
            try:
                parsed = json.loads(r["unit_ids_json"])
                if isinstance(parsed, list):
                    unit_ids = [str(x) for x in parsed]
            except Exception as e:  # noqa: BLE001
                logger.debug("Failed to parse unit_ids_json for exam %s: %s", r["exam_id"], e)

        exams.append(
            ExamDate(
                exam_id=r["exam_id"],
                course_key=r["course_key"],
                date=_parse_exam_date(r["date"]),
                units=unit_ids,
            )
        )

    # 5. Build plan
    plan_id = new_ulid()
    plan = build_plan(
        candidates=candidates,
        exams=exams,
        introduced=introduced,
        active_courses=active_courses,
        now=ctx.now,
        tz=EASTERN,
        plan_id=plan_id,
        producer=PRODUCER,
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

    summary.done += 1
    return summary
