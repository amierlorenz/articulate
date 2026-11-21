"""Missed days: the review backlog and the idle nudge (§7.1 rule 4).

Inputs are Encore's state (due dates) and the attempts ledger (which local days had a session).
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo

from articulate.core.handoff_schemas import State

REVIEW_SECONDS = 15          # one due review, on average, inside the day's budget
IDLE_DAYS = 3


@dataclass(frozen=True)
class Backlog:
    review_limit: int | None = None
    review_priority: list[str] = field(default_factory=list)
    new_paused: bool = False


def session_days(answered_at: list[datetime], tz: tzinfo) -> set[date]:
    return {a.astimezone(tz).date() for a in answered_at}


def due_count(state: State | None, until: datetime) -> int:
    """Introduced, unsuspended prompts due before the plan expires."""
    if state is None:
        return 0
    suspended = {c.conceptId for c in state.concepts if c.suspended}
    return sum(
        1 for p in state.prompts
        if p.introducedAt is not None and not p.suspended and p.conceptId not in suspended
        and p.due is not None and p.due <= until
    )


def review_fit(planned_minutes: int) -> int:
    return planned_minutes * 60 // REVIEW_SECONDS


def backlog(
    *, due: int, sessions: set[date], today: date, planned_minutes: int, ramp_exam_ids: list[str], started: bool
) -> Backlog:
    """After a day with no attempts, or while the backlog doesn't fit: limit reviews, ramp exams first.

    New stays paused until the due reviews fit the budget. Before the first session there is nothing to miss.
    """
    if not started:
        return Backlog()
    fit = review_fit(planned_minutes)
    missed = today - timedelta(days=1) not in sessions
    if not missed and due <= fit:
        return Backlog()
    return Backlog(review_limit=fit, review_priority=list(ramp_exam_ids), new_paused=due > fit)


def idle_streak_start(sessions: set[date], today: date, days: int = IDLE_DAYS) -> date | None:
    """First day of the current idle streak once it is `days` long; None before any session or while active."""
    past = [d for d in sessions if d < today]
    if not past:
        return None
    start = max(past) + timedelta(days=1)
    return start if (today - start).days >= days else None


def idle_task_id(start: date) -> str:
    return f"study:idle:{start.isoformat()}"
