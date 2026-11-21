"""Assemble the Phase 3 daily plan (§7.1 rules 1–8) from plain inputs. No model calls, no I/O."""
from dataclasses import dataclass, field
from datetime import UTC, datetime, tzinfo

from articulate.core.handoff_schemas import ExamFocus, Header, PlanNew, State
from articulate.plan import backlog as bl
from articulate.plan import contrasts as ct
from articulate.plan import ramp as rp
from articulate.plan.schema import DailyPlan
from articulate.plan.simple import (
    BUDGET_TARGET_MINUTES,
    FREE_RECALL_MIN,
    INTERVAL_CAP_DAYS,
    NEW_CAP,
    Candidate,
    ExamDate,
    plan_date,
    valid_until,
)

READING_SHARE_MAX = 0.2


@dataclass(frozen=True)
class Correction:
    """A prompt re-issued with a new id (re-intake, #23)."""

    concept_id: str
    prompt_id: str


@dataclass
class PlanInputs:
    candidates: list[Candidate]
    exams: list[ExamDate]
    introduced: set[str]
    active_courses: list[str]
    now: datetime
    tz: tzinfo
    plan_id: str
    producer: str
    state: State | None = None
    attempts: list[tuple[str, datetime]] = field(default_factory=list)   # (concept_id, answered_at)
    corrections: list[Correction] = field(default_factory=list)
    postclass_skipped: list[str] = field(default_factory=list)          # concept ids, in quiz order
    contrasts: list[ct.ContrastPrompt] = field(default_factory=list)
    reading_share: float = 0.0                                          # Phase 5 reading slot hook


def _state_maps(state: State | None) -> tuple[dict[str, datetime], set[str], dict[str, int]]:
    """Concept introduction times, prompts Encore knows, and introduced prompts per concept."""
    intro: dict[str, datetime] = {}
    known: set[str] = set()
    per_concept: dict[str, int] = {}
    if state is None:
        return intro, known, per_concept
    for c in state.concepts:
        if c.introducedAt is not None:
            intro[c.conceptId] = c.introducedAt
    for p in state.prompts:
        if p.introducedAt is not None:
            known.add(p.promptId)
            intro[p.conceptId] = min(intro.get(p.conceptId, p.introducedAt), p.introducedAt)
            if not p.suspended:
                per_concept[p.conceptId] = per_concept.get(p.conceptId, 0) + 1
    return intro, known, per_concept


def _corrections(corrections: list[Correction], known: set[str]) -> list[PlanNew]:
    by_concept: dict[str, list[str]] = {}
    for c in corrections:
        if c.prompt_id not in known:
            by_concept.setdefault(c.concept_id, []).append(c.prompt_id)
    return [PlanNew(conceptId=cid, promptIds=sorted(ids), reason="correction") for cid, ids in by_concept.items()]


def build_daily_plan(inp: PlanInputs) -> DailyPlan:
    today = plan_date(inp.now, inp.tz)
    until = valid_until(inp.now, inp.tz)
    courses = sorted(set(inp.active_courses))
    in_courses = [c for c in inp.candidates if c.course_key in courses]
    intro_at, known, per_concept = _state_maps(inp.state)

    active_ramps = rp.ramps(inp.exams, today, courses)
    planned = rp.RAMP_BUDGET_MINUTES if active_ramps else BUDGET_TARGET_MINUTES

    sessions = bl.session_days([t for _, t in inp.attempts], inp.tz)
    back = bl.backlog(
        due=bl.due_count(inp.state, until),
        sessions=sessions,
        today=today,
        planned_minutes=planned,
        ramp_exam_ids=[r.exam.exam_id for r in active_ramps],
        started=bool(known),
    )

    # Outside the cap: corrections first, then the concepts of a skipped post-class quiz.
    new = _corrections(inp.corrections, known)
    lead: list[PlanNew] = []
    if not back.new_paused:
        seen: set[str] = set()
        for cid in inp.postclass_skipped:
            if cid not in inp.introduced and cid not in seen:
                seen.add(cid)
                lead.append(PlanNew(conceptId=cid, promptIds=[], reason="postclassSkipped"))
    new += lead

    if not back.new_paused:
        reviews: dict[str, list[datetime]] = {}
        for cid, t in inp.attempts:
            reviews.setdefault(cid, []).append(t)
        picked = ct.pick(inp.contrasts, {c.concept_id: c for c in in_courses}, intro_at, reviews, known,
                         active_ramps)
        taken = inp.introduced | {n.conceptId for n in lead}
        new += rp.order_new(in_courses, inp.exams, active_ramps, today, taken, cap=NEW_CAP - len(picked))
        new += picked

    passed = rp.finals_passed(inp.exams, today)
    return DailyPlan(
        header=Header(
            schema="encore.plan",
            version=1,
            id=inp.plan_id,
            created_at=inp.now.astimezone(UTC).replace(microsecond=0),
            producer=inp.producer,
        ),
        kind="daily",
        date=today,
        validUntil=until,
        reviewScope=courses,
        new=new,
        examFocus=[ExamFocus(examId=r.exam.exam_id, rampDay=r.day) for r in active_ramps],
        budgetMinutes={"target": BUDGET_TARGET_MINUTES, "planned": planned},
        kindsMix={"freeRecallMin": FREE_RECALL_MIN},
        readingShare=0.0 if active_ramps else min(inp.reading_share, READING_SHARE_MAX),
        intervalCaps=[{"courseId": c, "maxDays": INTERVAL_CAP_DAYS} for c in courses if c not in passed],
        practiceTest=rp.practice_test(active_ramps, in_courses, per_concept),
        reviewQuiz=rp.review_quiz(active_ramps, in_courses, inp.introduced),
        reviewLimit=back.review_limit,
        reviewPriority=back.review_priority,
        newPaused=back.new_paused,
        holdNewForExams=[r.exam.exam_id for r in active_ramps if r.held],
    )
