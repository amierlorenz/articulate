"""Exam ramps, practice tests, the review quiz, the 2-day hold and the finals boost (§7.1 rules 1–3, 8).

Plain code over the library rows and the exam dates (§3.14). A ramp day is the number of days until the exam:
day 10 is ten days out, day 1 is the day before.
"""
import json
from dataclasses import dataclass
from datetime import date

from articulate.core.handoff_schemas import PlanNew
from articulate.plan.schema import PracticeTest, ReviewQuiz
from articulate.plan.simple import (
    NEW_CAP,
    Candidate,
    ExamDate,
    exam_covers,
    tier_rank,
)

RAMP_DAYS = 10
FINAL_RAMP_DAYS = 14
PRACTICE_TEST_DAYS = (9, 6, 3)
REVIEW_QUIZ_DAY = 1
HOLD_NEW_DAYS = 2                  # ramp days 2 and 1: no new for that exam
RAMP_BUDGET_MINUTES = 35
PRACTICE_ITEMS_MIN = 15
PRACTICE_ITEMS_MAX = 20
REVIEW_QUIZ_MAX = 10


@dataclass(frozen=True)
class Ramp:
    exam: ExamDate
    day: int

    @property
    def held(self) -> bool:
        return self.day <= HOLD_NEW_DAYS


def is_final(exam: ExamDate) -> bool:
    return exam.kind == "final" or "final" in (exam.name or "").lower()


def is_midterm(exam: ExamDate) -> bool:
    return exam.kind == "midterm" or "midterm" in (exam.name or "").lower()


def in_scope(exam: ExamDate, cand: Candidate) -> bool:
    """A final covers every unit of its course (rule 3); other exams cover their units."""
    if is_final(exam):
        return exam.course_key == cand.course_key
    return exam_covers(exam, cand)


def ramps(exams: list[ExamDate], today: date, courses: list[str]) -> list[Ramp]:
    """Exams inside their ramp (10 days, 14 for a final), nearest first."""
    out = []
    for e in exams:
        if e.date is None or e.course_key not in courses:
            continue
        day = (e.date - today).days
        if 1 <= day <= (FINAL_RAMP_DAYS if is_final(e) else RAMP_DAYS):
            out.append(Ramp(e, day))
    return sorted(out, key=lambda r: (r.day, r.exam.exam_id))


def finals_passed(exams: list[ExamDate], today: date) -> set[str]:
    """Courses whose final is behind them (rule 8: the interval cap is dropped)."""
    return {e.course_key for e in exams if is_final(e) and e.date is not None and e.date < today}


def emphasis_units(tier_reasons_json: str | None) -> int:
    """Distinct units a concept's tier reasons come from ([{signal, quote, sourceFileId, page, unitId?}])."""
    if not tier_reasons_json:
        return 0
    try:
        reasons = json.loads(tier_reasons_json)
    except ValueError:
        return 0
    if not isinstance(reasons, list):
        return 0
    return len({r.get("unitId") or r.get("unit") for r in reasons if isinstance(r, dict)} - {None, ""})


def is_boosted(cand: Candidate, tier_reasons_json: str | None, exams: list[ExamDate]) -> bool:
    """Emphasised in 2+ units, or in the units of a midterm of its course."""
    if emphasis_units(tier_reasons_json) >= 2:
        return True
    return any(is_midterm(e) and exam_covers(e, cand) for e in exams)


def nearest_exam_date(cand: Candidate, exams: list[ExamDate], today: date) -> date | None:
    """Soonest dated exam on or after today whose scope holds the concept (a final holds every unit)."""
    return min((e.date for e in exams if e.date is not None and e.date >= today and in_scope(e, cand)), default=None)


def _rank(cand: Candidate, ramp: Ramp | None) -> int:
    rank = tier_rank(cand.tier)
    assert rank is not None
    if ramp is not None and is_final(ramp.exam) and cand.boosted:
        rank = max(0, rank - 1)
    return rank


def order_new(
    candidates: list[Candidate],
    exams: list[ExamDate],
    active_ramps: list[Ramp],
    today: date,
    introduced: set[str],
    cap: int = NEW_CAP,
) -> list[PlanNew]:
    """Ramp concepts first (nearest exam first), then the Phase 2 order; nothing for an exam in its 2-day hold.

    Background still only gets the room that is left, unless a final's boost lifts it to standard.
    """
    held = [r for r in active_ramps if r.held]
    open_ramps = [r for r in active_ramps if not r.held]
    eligible = [
        c for c in candidates
        if c.concept_id not in introduced
        and tier_rank(c.tier) is not None
        and not any(in_scope(r.exam, c) for r in held)
    ]

    def key(c: Candidate) -> tuple:
        ramp = next((r for r in open_ramps if in_scope(r.exam, c)), None)
        rank = _rank(c, ramp)
        exam = nearest_exam_date(c, exams, today)
        return (
            rank == 2, ramp is None, ramp.day if ramp else 0, rank,
            exam is None, exam or date.max, c.created_at or "", c.concept_id,
        )

    ordered = sorted(eligible, key=key)[:cap]
    return [PlanNew(conceptId=c.concept_id, promptIds=[], reason=c.tier) for c in ordered]


def practice_test(active_ramps: list[Ramp], candidates: list[Candidate], prompts_known: dict[str, int]
                  ) -> PracticeTest | None:
    """On ramp days 9, 6 and 3 of the nearest such exam: 15–20 items from the introduced prompts in scope."""
    ramp = next((r for r in active_ramps if r.day in PRACTICE_TEST_DAYS), None)
    if ramp is None:
        return None
    known = sum(prompts_known.get(c.concept_id, 0) for c in candidates if in_scope(ramp.exam, c))
    return PracticeTest(examId=ramp.exam.exam_id,
                        itemCount=min(PRACTICE_ITEMS_MAX, max(PRACTICE_ITEMS_MIN, known)))


def review_quiz(active_ramps: list[Ramp], candidates: list[Candidate], introduced: set[str]) -> ReviewQuiz | None:
    """The day before an exam: its core concepts (a final's boosted ones count), introduced first, short."""
    ramp = next((r for r in active_ramps if r.day == REVIEW_QUIZ_DAY), None)
    if ramp is None:
        return None
    core = [c for c in candidates
            if in_scope(ramp.exam, c) and tier_rank(c.tier) is not None and _rank(c, ramp) == 0]
    core.sort(key=lambda c: (c.concept_id not in introduced, c.created_at or "", c.concept_id))
    return ReviewQuiz(examId=ramp.exam.exam_id, conceptIds=[c.concept_id for c in core[:REVIEW_QUIZ_MAX]])
