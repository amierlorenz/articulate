"""Missed days and the idle nudge (#70, SPEC-requirements 3.4)."""
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from articulate.core.handoff_schemas import Header, State, StateConcept, StatePrompt
from articulate.plan.backlog import (
    Backlog,
    backlog,
    due_count,
    idle_streak_start,
    idle_task_id,
    session_days,
)
from articulate.plan.daily import Correction, PlanInputs, build_daily_plan
from articulate.plan.simple import Candidate, ExamDate

EASTERN = ZoneInfo("America/New_York")
TODAY = date(2026, 11, 21)
NOW = datetime.combine(TODAY, time(3, 5), tzinfo=EASTERN)
INTRO = datetime(2026, 11, 1, 14, tzinfo=UTC)


def _state(due: int, not_due: int = 0) -> State:
    prompts = [
        StatePrompt(promptId=f"old{i}.flashcard.1", conceptId=f"old{i}", due=datetime(2026, 11, 20, tzinfo=UTC),
                    introducedAt=INTRO)
        for i in range(due)
    ] + [
        StatePrompt(promptId=f"later{i}.flashcard.1", conceptId=f"later{i}", due=datetime(2026, 12, 20, tzinfo=UTC),
                    introducedAt=INTRO)
        for i in range(not_due)
    ]
    return State(
        header=Header(schema="encore.state", version=1, id="s", created_at=NOW, producer="encore"),
        exportedAt=NOW,
        concepts=[StateConcept(conceptId=p.conceptId, introducedAt=INTRO) for p in prompts],
        prompts=prompts,
    )


# --- backlog rule -----------------------------------------------------------------------------------------------

def test_due_count_ignores_suspended_and_not_yet_due():
    state = _state(due=3, not_due=2)
    state.prompts[0].suspended = True
    state.concepts[1].suspended = True
    assert due_count(state, datetime(2026, 11, 22, 2, tzinfo=EASTERN)) == 1
    assert due_count(None, NOW) == 0


def test_a_day_with_no_attempts_limits_reviews_to_what_fits_and_ramps_go_first():
    out = backlog(due=30, sessions={TODAY - timedelta(days=2)}, today=TODAY, planned_minutes=20,
                  ramp_exam_ids=["csc301:test3"], started=True)
    assert out == Backlog(review_limit=80, review_priority=["csc301:test3"], new_paused=False)


def test_new_pauses_while_the_backlog_does_not_fit():
    out = backlog(due=200, sessions=set(), today=TODAY, planned_minutes=35, ramp_exam_ids=[], started=True)
    assert out == Backlog(review_limit=140, review_priority=[], new_paused=True)
    # Studied yesterday, but the backlog still doesn't fit: new stays paused.
    out = backlog(due=200, sessions={TODAY - timedelta(days=1)}, today=TODAY, planned_minutes=35,
                  ramp_exam_ids=[], started=True)
    assert out.new_paused and out.review_limit == 140


def test_studied_yesterday_and_it_fits_means_no_limit():
    out = backlog(due=50, sessions={TODAY - timedelta(days=1)}, today=TODAY, planned_minutes=20,
                  ramp_exam_ids=["x"], started=True)
    assert out == Backlog()


def test_nothing_to_miss_before_the_first_session():
    assert backlog(due=0, sessions=set(), today=TODAY, planned_minutes=20, ramp_exam_ids=[], started=False) == Backlog()


def test_paused_plan_keeps_corrections_and_drops_other_new():
    plan = build_daily_plan(PlanInputs(
        candidates=[Candidate("fresh", "csc301", "u", None, "core")],
        exams=[ExamDate("csc301:test3", "csc301", date(2026, 11, 30), units=["u"], kind="test")],
        introduced={"old0"},
        active_courses=["csc301"],
        now=NOW, tz=EASTERN, plan_id="p", producer="t",
        state=_state(due=200),
        attempts=[("old0", datetime(2026, 11, 18, 15, tzinfo=UTC))],
        corrections=[Correction("old0", "old0.flashcard.2")],
        postclass_skipped=["fresh"],
    ))
    assert plan.reviewLimit == 140
    assert plan.reviewPriority == ["csc301:test3"]
    assert plan.newPaused is True
    assert [(n.conceptId, n.reason) for n in plan.new] == [("old0", "correction")]


# --- idle streaks -----------------------------------------------------------------------------------------------

def test_sessions_are_local_days():
    late = datetime(2026, 11, 21, 3, tzinfo=UTC)   # 22:00 Eastern on Nov 20
    assert session_days([late], EASTERN) == {date(2026, 11, 20)}


def test_three_idle_days_start_a_streak_named_by_its_first_day():
    sessions = {date(2026, 11, 17)}
    assert idle_streak_start(sessions, date(2026, 11, 20)) is None        # Nov 18, 19: two idle days
    assert idle_streak_start(sessions, date(2026, 11, 21)) == date(2026, 11, 18)
    assert idle_streak_start(sessions, date(2026, 11, 25)) == date(2026, 11, 18)   # same streak, same id
    assert idle_task_id(date(2026, 11, 18)) == "study:idle:2026-11-18"


def test_no_streak_before_any_session():
    assert idle_streak_start(set(), TODAY) is None
