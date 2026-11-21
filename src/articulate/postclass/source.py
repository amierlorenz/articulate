"""Which lectures want a post-class quiz, and their archived class windows (§7.1, #71).

Ended classes come from the Classes calendar (events that ended in the last 18 h with an `active` course key). A
lecture's recording is the class span `pocket.poll` already cut and archived (it applied the Private rules and the
calendar match, §3.5), so this module reads the archive and never calls Pocket itself.
"""
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any

from articulate.calendar.client import CalendarClient, CalendarEvent
from articulate.calendar.match import normalize_course_key
from articulate.core.clock import EASTERN
from articulate.core.pipeline import RunContext
from articulate.inbox.transcript import Transcript, load_archived

logger = logging.getLogger(__name__)

LOOKBACK = timedelta(hours=18)
CLASS_TAIL = timedelta(minutes=10)  # class window = start → end + 10 min (#11)
TAKEOVER_AT = time(2, 0)

EventLoader = Callable[[RunContext, datetime, datetime], list[CalendarEvent]]


@dataclass
class Lecture:
    lecture_id: str
    course_key: str
    event: CalendarEvent

    @property
    def class_end(self) -> datetime:
        return self.event.end


@dataclass
class ClassWindow:
    recording_id: str
    transcript: Transcript  # cut to the class window, on the recording's timeline
    start_sec: float
    end_sec: float


def lecture_id(course_key: str, start: datetime) -> str:
    """`<course>.l.<YYYY-MM-DD>`, the class's local date."""
    return f"{course_key}.l.{start.astimezone(EASTERN).date().isoformat()}"


def takeover_due(class_end: datetime) -> datetime:
    """The 02:00 local run that takes the lecture over: the first 02:00 after the class ended."""
    local = class_end.astimezone(EASTERN)
    day = local.date() if local.time() < TAKEOVER_AT else local.date() + timedelta(days=1)
    return datetime.combine(day, TAKEOVER_AT, tzinfo=EASTERN)


def active_courses(ctx: RunContext) -> set[str]:
    if ctx.config is not None and getattr(ctx.config, "courses", None) is not None and ctx.config.courses.courses:
        return {k for k, c in ctx.config.courses.courses.items() if c.active}
    with ctx.db.connect() as conn:
        return {r["course_key"] for r in conn.execute("SELECT course_key FROM courses WHERE active = 1;")}


def calendar_events(ctx: RunContext, time_min: datetime, time_max: datetime) -> list[CalendarEvent]:
    """Classes-calendar events between the two times (the live Google Calendar read)."""
    cal_cfg = ctx.config.articulate.calendar if ctx.config is not None and ctx.config.articulate else None
    client = CalendarClient(config=cal_cfg, health=ctx.health)
    calendar = cal_cfg.classes_calendar if cal_cfg is not None else "Classes"
    return client.list_events(calendar, time_min, time_max)


def ended_lectures(ctx: RunContext, events: list[CalendarEvent]) -> list[Lecture]:
    """Classes events that ended in the last 18 h, with an active course key, one per lecture id."""
    courses = active_courses(ctx)
    out: dict[str, Lecture] = {}
    for evt in sorted(events, key=lambda e: e.start):
        if not (ctx.now - LOOKBACK < evt.end <= ctx.now):
            continue
        key = normalize_course_key(evt.description)
        if not key or key not in courses:
            continue
        lid = lecture_id(key, evt.start)
        out.setdefault(lid, Lecture(lid, key, evt))  # one quiz per lecture: the day's first class wins
    return list(out.values())


def find_class_window(ctx: RunContext, lecture: Lecture) -> ClassWindow | None:
    """The archived class span that matches this lecture's event, cut to its class window; None until archived."""
    win_start = lecture.event.start
    win_end = lecture.event.end + CLASS_TAIL
    with ctx.db.connect() as conn:
        rows = conn.execute(
            """
            SELECT native_id, payload_json FROM items
            WHERE source = 'pocket' AND kind = 'span' AND privacy = 'class' AND course_key = ?;
            """,
            (lecture.course_key,),
        ).fetchall()

    best: tuple[float, ClassWindow] | None = None
    for row in rows:
        payload: dict[str, Any] = json.loads(row["payload_json"] or "{}")
        if payload.get("span_kind", "class") != "class":
            continue
        rec_id = str(payload.get("recording_id") or row["native_id"].split(":")[0])
        transcript = load_archived(rec_id)
        if transcript is None or not transcript.segments:
            continue
        start_sec = float(payload.get("start_sec") or 0.0)
        end_sec = float(payload.get("end_sec") or transcript.duration or 0.0)
        rec_at = transcript.recorded_at()
        abs_start = max(rec_at + timedelta(seconds=start_sec), win_start)
        abs_end = min(rec_at + timedelta(seconds=end_sec), win_end)
        overlap = (abs_end - abs_start).total_seconds()
        if overlap <= 0:
            continue
        # keep the span to the event's own window, so another class in the same recording never leaks in
        lo = (abs_start - rec_at).total_seconds()
        hi = (abs_end - rec_at).total_seconds()
        window = transcript.window(lo, hi)
        if not window.segments:
            continue
        cand = ClassWindow(rec_id, window, lo, hi)
        if best is None or overlap > best[0]:
            best = (overlap, cand)
    return best[1] if best else None
