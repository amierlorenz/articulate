"""Phase 2 daily plan rules (§3.7 `encore.plan` v1, SPEC-requirements 2.39, Daily rhythm #9).

Plain code, no model calls. Each rule is its own small function so Phase 3 (#70: ramp, practice tests, missed
days) can replace or wrap one rule at a time. Phase 3 assembles the daily plan in `plan.daily`.
"""
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, tzinfo

from articulate.core.handoff_schemas import Header, Plan, PlanNew, State

NEW_CAP = 10
BUDGET_TARGET_MINUTES = 20
FREE_RECALL_MIN = 0.5
INTERVAL_CAP_DAYS = 30
PLAN_EXPIRES_AT = time(2, 0)
TIER_ORDER = ("core", "standard", "background")


@dataclass(frozen=True)
class Candidate:
    """A concept that could be introduced: active, with at least one active prompt."""

    concept_id: str
    course_key: str
    unit_id: str | None
    unit_name: str | None
    tier: str | None
    created_at: str | None = None
    boosted: bool = False    # emphasised in 2+ units or on a midterm: one tier up in a final's ordering (#70)


@dataclass(frozen=True)
class ExamDate:
    exam_id: str
    course_key: str
    date: date | None
    units: list[str] = field(default_factory=list)   # unit ids or unit names; empty = the whole course
    kind: str | None = None                          # test | midterm | final | ...
    name: str | None = None


def introduced_ids(state: State | None) -> set[str]:
    """Concepts Encore has introduced (or suspended). No state yet → nothing counts as introduced."""
    if state is None:
        return set()
    ids = {c.conceptId for c in state.concepts if c.introducedAt is not None or c.suspended}
    ids |= {p.conceptId for p in state.prompts if p.introducedAt is not None}
    return ids


def exam_covers(exam: ExamDate, cand: Candidate) -> bool:
    if exam.course_key != cand.course_key:
        return False
    if not exam.units:
        return True
    return cand.unit_id in exam.units or (cand.unit_name is not None and cand.unit_name in exam.units)


def nearest_exam(cand: Candidate, exams: list[ExamDate], today: date) -> date | None:
    """Soonest dated exam on or after today that covers the concept's unit."""
    dates = [e.date for e in exams if e.date is not None and e.date >= today and exam_covers(e, cand)]
    return min(dates, default=None)


def tier_rank(tier: str | None) -> int | None:
    return TIER_ORDER.index(tier) if tier in TIER_ORDER else None


def order_new(
    candidates: list[Candidate], exams: list[ExamDate], today: date, introduced: set[str], cap: int = NEW_CAP
) -> list[PlanNew]:
    """Not-yet-introduced concepts: core → standard → background, nearest exam first, at most `cap`.

    Background sorts after every core and standard concept, so it only gets the room that is left.
    Concepts with no dated exam sort after those with one.
    """
    eligible = [c for c in candidates if c.concept_id not in introduced and tier_rank(c.tier) is not None]

    def key(c: Candidate) -> tuple:
        exam = nearest_exam(c, exams, today)
        return (tier_rank(c.tier), exam is None, exam or date.max, c.created_at or "", c.concept_id)

    ordered = sorted(eligible, key=key)[:cap]
    return [PlanNew(conceptId=c.concept_id, promptIds=[], reason=c.tier) for c in ordered]


def plan_date(now: datetime, tz: tzinfo) -> date:
    return now.astimezone(tz).date()


def valid_until(now: datetime, tz: tzinfo) -> datetime:
    """02:00 local on the day after the plan's date (the next 02:00 for a run after 02:00)."""
    return datetime.combine(plan_date(now, tz) + timedelta(days=1), PLAN_EXPIRES_AT, tzinfo=tz)


def interval_caps(active_courses: list[str]) -> list[dict]:
    return [{"courseId": c, "maxDays": INTERVAL_CAP_DAYS} for c in active_courses]


def build_plan(
    *,
    candidates: list[Candidate],
    exams: list[ExamDate],
    introduced: set[str],
    active_courses: list[str],
    now: datetime,
    tz: tzinfo,
    plan_id: str,
    producer: str,
) -> Plan:
    courses = sorted(set(active_courses))
    in_scope = [c for c in candidates if c.course_key in courses]
    return Plan(
        header=Header(
            schema="encore.plan",
            version=1,
            id=plan_id,
            created_at=now.astimezone(UTC).replace(microsecond=0),
            producer=producer,
        ),
        kind="daily",
        date=plan_date(now, tz),
        validUntil=valid_until(now, tz),
        reviewScope=courses,
        new=order_new(in_scope, exams, plan_date(now, tz), introduced),
        budgetMinutes={"target": BUDGET_TARGET_MINUTES, "planned": BUDGET_TARGET_MINUTES},
        kindsMix={"freeRecallMin": FREE_RECALL_MIN},
        readingShare=0.0,
        intervalCaps=interval_caps(courses),
    )
