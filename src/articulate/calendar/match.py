"""Recording classification and calendar overlap rules (§3.2, §3.5, Issue #54).

Deciding tickets:
- Pocket for class lectures (#2): overlap >=50% or >=20 min, largest overlap wins,
  course key = first line of description.
- Model routing and budget (#11): class window = class start -> class end + 10 min,
  rest returned as personal spans.
- Voice commands for acting on recordings (#29): private_events title substring or calendar name -> Private.
- Non-class recordings with no command (#42): non-class event match returns title and time for meeting notes.
"""
import html
import re
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from articulate.calendar.client import CalendarEvent
from articulate.core.config import CalendarConfig, CoursesConfig
from articulate.core.pipeline import Privacy


class RecordingSpan(BaseModel):
    """A classified span of a recording (§3.5, Issue #11, Issue #54)."""

    model_config = ConfigDict(extra="ignore")

    start: datetime
    end: datetime
    privacy: Privacy
    course_key: str | None = None
    event_id: str | None = None
    start_offset_seconds: float = 0.0
    end_offset_seconds: float = 0.0


class ClassificationResult(BaseModel):
    """Result of classifying a recording against calendar events (§3.2, §3.5, Issue #54)."""

    model_config = ConfigDict(extra="ignore")

    privacy: Privacy
    course_key: str | None = None
    spans: list[RecordingSpan] = Field(default_factory=list)
    matched_event: CalendarEvent | None = None
    is_meeting: bool = False
    meeting_title: str | None = None
    meeting_time: tuple[datetime, datetime] | None = None


def normalize_course_key(description: str | None) -> str | None:
    """
    Normalise a calendar event description to a course key (Spec §3.2, PR #68).
    Rule:
    Take the event description, strip HTML (<br> and block tags end a line),
    take the first non-empty line, lowercase it, and drop everything but a–z and 0–9.
    Returns normalized course key string, or None if no valid course key found.
    """
    if not description:
        return None

    # Replace <br> and block tags with newlines
    text = description
    block_tags = (
        r"</?(?:p|div|br|li|ul|ol|tr|td|th|h[1-6]|table|blockquote|section|article)[^>]*>"
    )
    text = re.sub(block_tags, "\n", text, flags=re.IGNORECASE)

    # Strip any remaining HTML tags
    text = re.sub(r"<[^>]+>", "", text)

    # Decode HTML entities
    text = html.unescape(text)

    # Split lines and find first non-empty line
    lines = text.splitlines()
    for line in lines:
        cleaned_line = line.strip()
        if not cleaned_line:
            continue
        # Lowercase and keep only a-z and 0-9
        normalized = re.sub(r"[^a-z0-9]", "", cleaned_line.lower())
        if normalized:
            return normalized

    return None


def _ensure_tz(dt: datetime) -> datetime:
    """Ensure datetime is timezone-aware (assume UTC if naive)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def calculate_overlap(
    rec_start: datetime,
    rec_duration_s: float,
    evt_start: datetime,
    evt_end: datetime,
) -> tuple[float, float]:
    """
    Calculate overlap between a recording and a calendar event in seconds and as a fraction of recording duration.
    Returns: (overlap_seconds, overlap_fraction)
    """
    rec_s = _ensure_tz(rec_start)
    rec_e = rec_s + timedelta(seconds=max(rec_duration_s, 0.0))
    evt_s = _ensure_tz(evt_start)
    evt_e = _ensure_tz(evt_end)

    overlap_start = max(rec_s, evt_s)
    overlap_end = min(rec_e, evt_e)

    if overlap_start < overlap_end:
        overlap_seconds = (overlap_end - overlap_start).total_seconds()
    else:
        overlap_seconds = 0.0

    pct = (overlap_seconds / rec_duration_s) if rec_duration_s > 0 else 0.0
    return overlap_seconds, pct


def is_overlap_sufficient(overlap_seconds: float, overlap_pct: float) -> bool:
    """
    Overlap rule from Issue #2 and Spec 1.3:
    At least 50% of the recording or at least 20 minutes (1200 seconds).
    """
    return overlap_pct >= 0.5 or overlap_seconds >= 1200.0


def split_class_window(
    rec_start: datetime,
    rec_duration_s: float,
    event: CalendarEvent,
    course_key: str,
) -> list[RecordingSpan]:
    """
    Split a recording into class window and personal spans (Spec §3.5, Issue #11, Issue #54).
    Class window = class start -> class end + 10 min.
    The rest is returned as personal spans.
    """
    rec_s = _ensure_tz(rec_start)
    rec_e = rec_s + timedelta(seconds=rec_duration_s)

    evt_s = _ensure_tz(event.start)
    evt_e = _ensure_tz(event.end)
    win_start = evt_s
    win_end = evt_e + timedelta(minutes=10)

    spans: list[RecordingSpan] = []

    # Lead-in (personal): before class start
    if rec_s < win_start:
        lead_end = min(rec_e, win_start)
        if lead_end > rec_s:
            spans.append(
                RecordingSpan(
                    start=rec_s,
                    end=lead_end,
                    privacy=Privacy.PERSONAL,
                    start_offset_seconds=(rec_s - rec_s).total_seconds(),
                    end_offset_seconds=(lead_end - rec_s).total_seconds(),
                )
            )

    # Class span: within class window
    class_start = max(rec_s, win_start)
    class_end = min(rec_e, win_end)
    if class_start < class_end:
        spans.append(
            RecordingSpan(
                start=class_start,
                end=class_end,
                privacy=Privacy.CLASS,
                course_key=course_key,
                event_id=event.id,
                start_offset_seconds=(class_start - rec_s).total_seconds(),
                end_offset_seconds=(class_end - rec_s).total_seconds(),
            )
        )

    # Tail (personal): after class end + 10 min
    if rec_e > win_end:
        tail_start = max(rec_s, win_end)
        if rec_e > tail_start:
            spans.append(
                RecordingSpan(
                    start=tail_start,
                    end=rec_e,
                    privacy=Privacy.PERSONAL,
                    start_offset_seconds=(tail_start - rec_s).total_seconds(),
                    end_offset_seconds=(rec_e - rec_s).total_seconds(),
                )
            )

    return spans


def is_private_event(event: CalendarEvent, config: CalendarConfig) -> bool:
    """Check if an event matches the private_events rule by calendar name or summary substring."""
    cal_lower = event.calendar.lower()

    # Check private_calendars
    for priv_cal in config.private_calendars:
        if priv_cal.lower() in cal_lower or cal_lower == priv_cal.lower():
            return True

    # Check private_events (calendar names or summary substrings)
    summary_lower = event.summary.lower()
    for priv_pattern in config.private_events:
        p_lower = priv_pattern.lower()
        if p_lower in summary_lower or p_lower in cal_lower or cal_lower == p_lower:
            return True

    return False


def match_private_event(
    rec_start: datetime,
    rec_duration_s: float,
    events: list[CalendarEvent],
    config: CalendarConfig,
) -> CalendarEvent | None:
    """
    Check if any private event overlaps the recording by the overlap rule (>=50% or >=20 min).
    Private fails closed. Returns the matching event if found.
    """
    for evt in events:
        if not is_private_event(evt, config):
            continue
        overlap_s, overlap_pct = calculate_overlap(rec_start, rec_duration_s, evt.start, evt.end)
        if is_overlap_sufficient(overlap_s, overlap_pct):
            return evt

    return None


def match_class_event(
    rec_start: datetime,
    rec_duration_s: float,
    events: list[CalendarEvent],
    classes_calendar: str = "Classes",
    valid_courses: set[str] | None = None,
) -> tuple[CalendarEvent, str, float] | None:
    """
    Match recording to a class event on the Classes calendar.
    - Event must be on the Classes calendar.
    - Course key comes from the first line of the event description.
    - If valid_courses is given, course key must be registered (fail closed).
    - Overlap >= 50% of recording or >= 20 min.
    - Largest overlap wins.
    Returns: (matched_event, course_key, overlap_seconds) or None
    """
    cal_target = classes_calendar.lower()
    best_match: tuple[CalendarEvent, str, float] | None = None
    max_overlap = -1.0

    for evt in events:
        if evt.calendar.lower() != cal_target:
            continue

        ck = normalize_course_key(evt.description)
        if not ck:
            continue

        if valid_courses is not None and ck not in valid_courses:
            continue

        overlap_s, overlap_pct = calculate_overlap(rec_start, rec_duration_s, evt.start, evt.end)
        if is_overlap_sufficient(overlap_s, overlap_pct) and overlap_s > max_overlap:
            max_overlap = overlap_s
            best_match = (evt, ck, overlap_s)

    return best_match


def match_meeting_event(
    rec_start: datetime,
    rec_duration_s: float,
    events: list[CalendarEvent],
    config: CalendarConfig,
) -> tuple[CalendarEvent, float] | None:
    """
    Match recording to a non-class, non-private calendar event for meeting notes.
    Overlap >= 50% or >= 20 min.
    Largest overlap wins.
    Returns: (matched_event, overlap_seconds) or None
    """
    cal_target = config.classes_calendar.lower()
    best_match: tuple[CalendarEvent, float] | None = None
    max_overlap = -1.0

    for evt in events:
        # Skip class calendar
        if evt.calendar.lower() == cal_target:
            continue
        # Skip private events
        if is_private_event(evt, config):
            continue

        overlap_s, overlap_pct = calculate_overlap(rec_start, rec_duration_s, evt.start, evt.end)
        if is_overlap_sufficient(overlap_s, overlap_pct) and overlap_s > max_overlap:
            max_overlap = overlap_s
            best_match = (evt, overlap_s)

    return best_match


def classify_recording(
    rec_start: datetime,
    rec_duration_s: float,
    events: list[CalendarEvent],
    config: CalendarConfig | None = None,
    courses_config: CoursesConfig | None = None,
    valid_courses: set[str] | None = None,
) -> ClassificationResult:
    """
    Classify a recording against calendar events according to all Articulate rules:
    1. Private event match (fails closed -> Privacy.PRIVATE)
    2. Class event match (Classes calendar + valid course key -> Privacy.CLASS + spans)
    3. Meeting event match (non-class event -> Privacy.PERSONAL, is_meeting=True)
    4. Unmatched (default personal recording -> Privacy.PERSONAL)
    """
    cfg = config or CalendarConfig()

    # Determine valid courses set
    known_courses: set[str] | None = valid_courses
    if known_courses is None and courses_config is not None:
        known_courses = set(courses_config.courses.keys())

    # 1. Private check (fail closed)
    priv_evt = match_private_event(rec_start, rec_duration_s, events, cfg)
    if priv_evt is not None:
        return ClassificationResult(
            privacy=Privacy.PRIVATE,
            matched_event=priv_evt,
        )

    # 2. Class match
    class_match = match_class_event(
        rec_start,
        rec_duration_s,
        events,
        classes_calendar=cfg.classes_calendar,
        valid_courses=known_courses,
    )
    if class_match is not None:
        matched_evt, course_key, _ = class_match
        spans = split_class_window(rec_start, rec_duration_s, matched_evt, course_key)
        return ClassificationResult(
            privacy=Privacy.CLASS,
            course_key=course_key,
            spans=spans,
            matched_event=matched_evt,
        )

    # 3. Meeting match
    meeting_match = match_meeting_event(rec_start, rec_duration_s, events, cfg)
    if meeting_match is not None:
        matched_evt, _ = meeting_match
        meeting_time = (_ensure_tz(matched_evt.start), _ensure_tz(matched_evt.end))
        return ClassificationResult(
            privacy=Privacy.PERSONAL,
            matched_event=matched_evt,
            is_meeting=True,
            meeting_title=matched_evt.summary,
            meeting_time=meeting_time,
        )

    # 4. Unmatched personal
    return ClassificationResult(
        privacy=Privacy.PERSONAL,
        is_meeting=False,
    )
